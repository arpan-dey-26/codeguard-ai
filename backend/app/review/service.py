"""The review orchestrator.

One method, ``review_pull_request``, that runs the whole Phase 3 pipeline in a
fixed order. The order is the design; each step exists to make the next one
safe.

    1  sync          fetch PR metadata and the changed-file set from GitHub
    2  analyse       Phase 2: diff → LineMap → AST (already done by the sync)
    3  supersede     mark older in-flight runs of this PR as superseded
    4  create run    a row, before the model is called, so a crash leaves a trace
    5  context       bounded, deterministic, from the analysis only
    6  model         one call, one response, all of it untrusted
    7  validate      every claim against the diff and the real file content
    8  persist       findings and rejections, COMMITTED
    9  publish       validated findings only, to GitHub
    10 record        comments, funnel, final status

**Why persist (8) comes before publish (9).** If publication fails, the
analysis survives: the run degrades to ``partial_success`` and every finding is
still in the dashboard. The reverse order loses the work whenever the process
dies in between, and leaves GitHub comments the dashboard cannot explain
(architecture.md §8.4).

**Where staleness is checked, and why in three places.** A push can arrive at
any moment.

* Before the model call — no point spending a quota unit on a dead commit.
* Before persistence — findings anchored to a superseded commit must not be
  written as publishable.
* Immediately before publication, against GitHub itself — the only check that
  closes the window, because the other two read our own database, which a
  webhook may not have updated yet.

Only the third is authoritative. The first two are cheap early exits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.ai.budget import ProviderCallBudget
from app.ai.contracts import ReviewRequest
from app.ai.prompt import PROMPT_VERSION
from app.analysis.artifact import PullRequestAnalysis
from app.core.errors import (
    CodeGuardError,
    ConfigurationError,
    ProviderBudgetExceededError,
    ThrottledError,
    TransientError,
)
from app.core.logging import get_logger
from app.db.repositories.reviews import (
    FindingRepository,
    PersistedFindings,
    ReviewCommentRepository,
    ReviewRunRepository,
)
from app.review.context import ContextBudget, build_review_context
from app.review.publisher import PublishOutcome, PublishResult, ReviewPublisher
from app.review.validator import FindingValidator, ValidationReport

logger = get_logger(__name__)


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    SKIPPED = "skipped"


class RunErrorCode:
    GITHUB_ACCESS_LOST = "github_access_lost"
    AI_UNAVAILABLE = "ai_unavailable"
    AI_BUDGET_EXCEEDED = "ai_budget_exceeded"
    AI_SCHEMA_INVALID = "ai_schema_invalid"
    AI_TRUNCATED = "ai_truncated"
    DIFF_UNAVAILABLE = "diff_unavailable"
    PUBLISH_FAILED = "publish_failed"
    STALE_HEAD = "stale_head"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """Everything the caller — and the report — needs to know about a run."""

    run_id: uuid.UUID | None
    status: str
    head_sha: str
    findings_emitted: int = 0
    findings_validated: int = 0
    findings_published: int = 0
    findings_rejected: int = 0
    rejection_counts: dict[str, int] = field(default_factory=dict)
    publish_outcome: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id) if self.run_id else None,
            "status": self.status,
            "head_sha": self.head_sha[:12],
            "emitted": self.findings_emitted,
            "validated": self.findings_validated,
            "published": self.findings_published,
            "rejected": self.findings_rejected,
            "rejections": self.rejection_counts,
            "publish": self.publish_outcome,
        }


class ReviewService:
    """Runs one review of one pull request at one commit."""

    def __init__(
        self,
        session: Any,
        *,
        client: Any,
        provider: Any,
        settings: Any,
        validator: FindingValidator | None = None,
        budget: ContextBudget | None = None,
    ) -> None:
        self._session = session
        self._client = client
        self._provider = provider
        self._settings = settings
        self._validator = validator or FindingValidator.from_settings(settings)
        self._budget = budget or ContextBudget.from_settings(settings)
        self._runs = ReviewRunRepository(session)
        self._findings = FindingRepository(session)
        self._comments = ReviewCommentRepository(session)

    async def review_pull_request(
        self,
        *,
        installation_id: int,
        repository_id: int,
        repository_full_name: str,
        pull_request_id: int,
        pull_request_number: int,
        head_sha: str,
        base_sha: str = "",
        analysis: PullRequestAnalysis,
        trigger: str = "synchronize",
        webhook_event_id: int | None = None,
        changed_file_ids: dict[str, int] | None = None,
    ) -> ReviewOutcome:
        owner, _, repo_name = repository_full_name.partition("/")

        # -- step 3: older runs of this PR are no longer current -----------
        await self._runs.supersede_older_runs(
            pull_request_id=pull_request_id, keep_head_sha=head_sha
        )

        # -- step 4: the run row, before anything can fail -----------------
        attempt = await self._runs.next_attempt(pull_request_id=pull_request_id, head_sha=head_sha)
        run_id = await self._runs.create(
            pull_request_id=pull_request_id,
            repository_id=repository_id,
            head_sha=head_sha,
            base_sha=base_sha,
            trigger=trigger if trigger in _TRIGGERS else "manual",
            webhook_event_id=webhook_event_id,
            attempt=attempt,
        )
        if run_id is None:
            # Another worker owns this exact (commit, attempt). Two reviews of
            # one commit is a duplicate-comment bug, so we stop rather than
            # race.
            logger.info("review.run.already_claimed", head_sha=head_sha[:12])
            return ReviewOutcome(run_id=None, status=RunStatus.SKIPPED, head_sha=head_sha)
        await self._session.commit()

        log = logger.bind(run_id=str(run_id), repository=repository_full_name)

        # -- step 5: the bounded context -----------------------------------
        if analysis.head_sha != head_sha:
            # The analysis describes a different commit. Anchoring findings
            # against it would place comments on lines that have moved.
            return await self._fail(
                run_id,
                head_sha,
                RunErrorCode.STALE_HEAD,
                "analysis head commit does not match the run's",
            )

        context = build_review_context(analysis, budget=self._budget)
        await self._runs.record_context(
            run_id,
            files_changed=analysis.total_files,
            files_analysed=len(context.included),
            files_skipped=analysis.total_files - len(context.included),
            context_truncated=context.truncated,
        )
        await self._session.commit()

        if context.is_empty:
            log.info("review.context.empty")
            settled = await self._settle(log, run_id, RunStatus.SUCCEEDED)
            await self._session.commit()
            return ReviewOutcome(run_id=run_id, status=settled, head_sha=head_sha)

        # -- step 6: the model call ----------------------------------------
        request = ReviewRequest(
            repository_full_name=repository_full_name,
            pull_request_number=pull_request_number,
            head_sha=head_sha,
            context_document=context.document,
            max_findings=self._settings.ai_max_findings,
        )
        # The documented free-tier ceiling, enforced rather than assumed.
        budgeted = ProviderCallBudget(self._provider, self._settings.ai_max_requests_per_review)
        try:
            result = await budgeted.review(request)
        except ProviderBudgetExceededError as exc:
            return await self._fail(run_id, head_sha, RunErrorCode.AI_BUDGET_EXCEEDED, str(exc))
        except ConfigurationError as exc:
            return await self._fail(run_id, head_sha, RunErrorCode.AI_UNAVAILABLE, str(exc))
        except (TransientError, ThrottledError):
            # Retryable: let the runner reschedule. The run row stays
            # ``running`` and the stale-job sweeper reclaims it if the worker
            # dies — deliberately *not* marked failed here, because a failed
            # row would make the retry look like a second review.
            raise
        except CodeGuardError as exc:
            return await self._fail(run_id, head_sha, RunErrorCode.AI_SCHEMA_INVALID, str(exc))

        await self._runs.record_provider_call(
            run_id,
            provider=getattr(self._provider, "name", "unknown"),
            model_name=result.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=result.usage.latency_ms,
            truncated=result.usage.truncated,
        )
        await self._session.commit()

        # -- step 7: validation --------------------------------------------
        analyses = {f.file_path: f for f in analysis.files}
        report = self._validator.validate(
            result.findings,
            context=context,
            analyses=analyses,
            head_sha=head_sha,
        )
        log.info("review.validated", **report.summary())

        # -- step 8: persist, and commit, BEFORE touching GitHub -----------
        persisted = await self._findings.persist_findings(
            run_id=run_id,
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            validated=report.validated,
            rejected=report.rejected,
            changed_file_ids=changed_file_ids,
        )
        await self._session.commit()

        # -- step 9: publish ------------------------------------------------
        publisher = ReviewPublisher(
            client=self._client,
            installation_id=installation_id,
            owner=owner,
            repo=repo_name,
            pull_number=pull_request_number,
            max_inline_comments=self._settings.review_max_inline_comments,
            publish_enabled=self._settings.review_publish_enabled,
        )
        publish = await publisher.publish(
            findings=report.validated,
            head_sha=head_sha,
            run_id=str(run_id),
            model_name=result.model,
            rejected_count=len(report.rejected),
            context_truncated=context.truncated,
            excluded_files=[entry.file_path for entry in context.excluded],
        )

        # -- step 10: record what happened ---------------------------------
        return await self._record_publication(
            run_id=run_id,
            head_sha=head_sha,
            report=report,
            publish=publish,
            persisted=persisted,
            emitted=result.raw_claim_count or len(result.findings),
            dropped=result.dropped_claim_count,
        )

    # -- recording ---------------------------------------------------------
    async def _record_publication(
        self,
        *,
        run_id: uuid.UUID,
        head_sha: str,
        report: ValidationReport,
        publish: PublishResult,
        persisted: PersistedFindings,
        emitted: int,
        dropped: int,
    ) -> ReviewOutcome:
        finding_ids = persisted.ids_by_fingerprint
        for comment in publish.posted:
            finding_id = finding_ids.get(comment.fingerprint)
            if finding_id is None:
                continue
            await self._comments.record(
                finding_id=finding_id,
                run_id=run_id,
                path=comment.path,
                line=comment.line,
                side=comment.side,
                commit_sha=head_sha,
                body_hash=comment.body_hash,
                github_review_id=publish.github_review_id,
                github_comment_id=comment.github_comment_id,
                html_url=comment.html_url,
            )

        demoted_ids = [
            finding_ids[fingerprint]
            for fingerprint in publish.demoted
            if fingerprint in finding_ids
        ]
        await self._findings.mark_summary_only(demoted_ids)

        # The funnel, with each stage meaning exactly one thing:
        #
        #   emitted          items in the model's array
        #   dropped          items the provider could not coerce at all
        #   dashboard_only   coerced claims the validator rejected
        #   persisted        ROWS written (not claims offered)
        #   inline           comments GitHub confirmed
        #   summary          validated findings demoted to the review body
        #
        # so that `emitted == dropped + validated_rows + dashboard_only` holds,
        # and `inline + summary <= validated_rows` — the shortfall being
        # findings already commented on by an earlier run, which are neither
        # posted again nor demoted.
        inline = len(publish.posted)
        summary = len(demoted_ids)
        await self._runs.record_funnel(
            run_id,
            emitted=emitted,
            persisted=persisted.total_rows,
            inline=inline,
            summary=summary,
            dashboard_only=persisted.rejected_rows,
            dropped=dropped,
        )

        status, error_code, error_message = _classify_publication(publish)
        status = await self._settle(
            logger.bind(run_id=str(run_id)),
            run_id,
            status,
            error_code=error_code,
            error_message=error_message,
            github_review_id=publish.github_review_id,
        )
        await self._session.commit()

        return ReviewOutcome(
            run_id=run_id,
            status=status,
            head_sha=head_sha,
            findings_emitted=emitted,
            findings_validated=len(report.validated),
            findings_published=inline,
            findings_rejected=persisted.rejected_rows,
            rejection_counts=report.rejection_counts,
            publish_outcome=publish.outcome,
            error_code=error_code,
            error_message=error_message,
        )

    async def _settle(
        self,
        log: Any,
        run_id: uuid.UUID,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        github_review_id: int | None = None,
    ) -> str:
        """Close the run and return the status it *actually* ended in.

        ``finish`` refuses to reopen a run that is already closed, which is
        almost always because a new push superseded this one while it was still
        working (architecture.md §13.4). When that happens the worker's own idea
        of the outcome is stale, and reporting it would put a second succeeded
        run on a pull request that has only one current commit. So the value the
        database settled on is what gets returned and logged, not the value this
        worker asked for.
        """
        settled = await self._runs.finish(
            run_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            github_review_id=github_review_id,
        )
        if settled is None:
            log.warning("review.run.missing_at_finish", requested=status)
            return status
        if settled != status:
            log.info("review.run.already_settled", requested=status, actual=settled)
        return settled

    async def _fail(
        self, run_id: uuid.UUID, head_sha: str, error_code: str, message: str
    ) -> ReviewOutcome:
        status = await self._settle(
            logger,
            run_id,
            RunStatus.FAILED,
            error_code=error_code,
            error_message=message,
        )
        await self._session.commit()
        logger.warning("review.run.failed", run_id=str(run_id), error_code=error_code)
        return ReviewOutcome(
            run_id=run_id,
            status=status,
            head_sha=head_sha,
            error_code=error_code,
            error_message=message,
        )


def _classify_publication(publish: PublishResult) -> tuple[str, str | None, str | None]:
    """Map a publication outcome onto the run's terminal status.

    ``partial_success`` is used deliberately for the cases where findings exist
    and are visible but did not all reach GitHub. Collapsing those into
    ``failed`` would report a run that produced real, persisted, visible work
    as a failure, and would make the failure-rate metric meaningless.
    """
    if publish.outcome in (PublishOutcome.PUBLISHED, PublishOutcome.NOTHING_TO_PUBLISH):
        return RunStatus.SUCCEEDED, None, None
    if publish.outcome == PublishOutcome.SKIPPED_DISABLED:
        return RunStatus.SUCCEEDED, None, None
    if publish.outcome == PublishOutcome.SKIPPED_STALE_HEAD:
        return (
            RunStatus.SUPERSEDED,
            RunErrorCode.STALE_HEAD,
            publish.error or "head commit moved before publication",
        )
    if publish.outcome in (PublishOutcome.PUBLISHED_PARTIAL, PublishOutcome.SUMMARY_ONLY):
        return RunStatus.PARTIAL_SUCCESS, RunErrorCode.PUBLISH_FAILED, publish.error
    return (
        RunStatus.PARTIAL_SUCCESS,
        RunErrorCode.PUBLISH_FAILED,
        publish.error or "publication failed; findings are persisted",
    )


_TRIGGERS = frozenset({"opened", "synchronize", "reopened", "ready_for_review", "manual"})


__all__ = ["ReviewOutcome", "ReviewService", "RunErrorCode", "RunStatus"]
