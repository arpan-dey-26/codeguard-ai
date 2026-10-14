"""Pull-request synchronisation.

Given a pull request identified by a webhook: authenticate as the App
installation, read the repository, the pull request and its changed-file list
from GitHub, persist the metadata, then (Phase 2) analyse the changed source
structurally at that exact commit.

What this service pointedly does **not** do: clone the repository, execute
anything from it, or send anything anywhere near an AI provider.

Two behaviours worth understanding:

*   **Head-SHA supersession.** Before persisting, the service compares the
    SHA the job was created for against the PR's current head. If a newer push
    has landed, the run is abandoned rather than writing a file list for a
    commit that is no longer current. The enqueue-time cancellation
    (§8.5) handles the common case; this check handles the race where a push
    lands while a job is already running.

*   **Persist-then-report.** The changed-file set is written before the
    summary counters, so a crash between the two leaves the files present and
    the counters stale — recoverable — rather than the reverse.

Phase 2 adds a final step: deterministic structural analysis at the same head
SHA. It runs *after* the supersession check, so it can never analyse source
belonging to a commit a newer push has replaced, and it is wrapped so that an
analysis failure never undoes a successful metadata sync.

Still absent, and deliberately: no AI provider is called and no GitHub review
comment is created.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.artifact import PullRequestAnalysis
from app.analysis.diff.models import FileChangeType
from app.analysis.pipeline import AnalysisConfig, AnalysisPipeline, ChangedFileInput
from app.analysis.source.provider import GitHubSourceProvider
from app.core.errors import PermanentInputError
from app.core.logging import get_logger
from app.db.repositories.github_entities import (
    ChangedFileRepository,
    InstallationRepository,
    PullRequestRepository,
    RepositoryRepository,
)
from app.github.client import GitHubClient
from app.github.models import GitHubChangedFile

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SyncResult:
    repository_full_name: str
    pull_request_number: int
    head_sha: str
    changed_files: int
    truncated: bool
    superseded: bool = False
    analysis: PullRequestAnalysis | None = None
    """The Phase 2 structural analysis, when it ran.

    Held in memory and handed to the caller. Deliberately **not** persisted:
    architecture.md §16 defines no Phase 2 table, the inputs are already stored
    or reproducible, and the artifact's backing objects carry third-party
    source text (§16.7). Phase 3 persists *findings*, which is different.
    """


class GitHubSyncService:
    """Synchronises one pull request's metadata from GitHub into PostgreSQL."""

    def __init__(
        self,
        session: AsyncSession,
        client: GitHubClient,
        *,
        analysis_config: AnalysisConfig | None = None,
        analyse: bool = True,
    ) -> None:
        self._session = session
        self._client = client
        self._analysis_config = analysis_config or AnalysisConfig()
        self._analyse = analyse
        self._installations = InstallationRepository(session)
        self._repositories = RepositoryRepository(session)
        self._pull_requests = PullRequestRepository(session)
        self._changed_files = ChangedFileRepository(session)

    async def sync_pull_request(
        self,
        *,
        installation_id: int,
        repository_full_name: str,
        pull_request_number: int,
        expected_head_sha: str | None = None,
    ) -> SyncResult:
        owner, _, repo_name = repository_full_name.partition("/")
        if not owner or not repo_name:
            # Permanent: a malformed identifier will still be malformed on a
            # retry, so this must not consume the retry budget.
            raise PermanentInputError(f"Malformed repository identifier: {repository_full_name!r}")

        log = logger.bind(
            installation_id=installation_id,
            repository=repository_full_name,
            pull_request_number=pull_request_number,
        )

        # 1. Repository metadata (also proves the installation token works).
        gh_repo = await self._client.get_repository(installation_id, owner, repo_name)

        # 2. Pull request metadata, which carries the authoritative head SHA.
        gh_pr = await self._client.get_pull_request(
            installation_id, owner, repo_name, pull_request_number
        )

        if expected_head_sha and gh_pr.head.sha and gh_pr.head.sha != expected_head_sha:
            # A newer push landed while this job waited or ran. Writing a file
            # list for a superseded commit would leave the database describing
            # a commit nobody is reviewing.
            log.info(
                "sync.pull_request.superseded",
                job_head_sha=expected_head_sha[:12],
                current_head_sha=gh_pr.head.sha[:12],
            )
            return SyncResult(
                repository_full_name=repository_full_name,
                pull_request_number=pull_request_number,
                head_sha=gh_pr.head.sha,
                changed_files=0,
                truncated=False,
                superseded=True,
            )

        # 3. Changed files — paginated, and possibly truncated by GitHub.
        files, truncated = await self._client.list_pull_request_files(
            installation_id, owner, repo_name, pull_request_number
        )

        # 4. Persist. One transaction, committed by the caller.
        internal_installation_id = await self._installations.upsert(
            github_installation_id=installation_id,
            account_login=gh_repo.owner_login,
            account_github_id=gh_repo.owner.id if gh_repo.owner else None,
            account_type=(
                gh_repo.owner.type if gh_repo.owner and gh_repo.owner.type else "Organization"
            ),
        )
        repository_id = await self._repositories.upsert_from_api(internal_installation_id, gh_repo)
        pull_request_id = await self._pull_requests.upsert(repository_id=repository_id, pr=gh_pr)
        stored = await self._changed_files.replace_for_commit(
            pull_request_id=pull_request_id,
            head_sha=gh_pr.head.sha,
            files=files,
        )
        await self._pull_requests.record_sync(
            pull_request_id=pull_request_id,
            head_sha=gh_pr.head.sha,
            changed_files_count=stored,
            truncated=truncated,
        )

        # **The transaction ends here, and that is a correctness requirement,
        # not a tidiness preference (Phase 5 fix).**
        #
        # Step 5 below fetches up to ``AnalysisConfig.max_files`` blobs from
        # GitHub, each with its own timeout and retry budget. Until Phase 5 the
        # commit lived in the *caller*, so those HTTP calls ran inside the write
        # transaction opened by the four statements above — holding a pooled
        # connection and the row locks taken by ``replace_for_commit``'s DELETE
        # for the whole fetch. With a pool of 5+10, a handful of concurrent
        # workers exhausted it and every request queued behind the analysis.
        #
        # ``app/db/session.py`` and ``app/jobs/runner.py`` both state the
        # invariant — no external call inside an open transaction — and this is
        # the line that makes it true here.
        await self._session.commit()

        log.info(
            "sync.pull_request.completed",
            head_sha=gh_pr.head.sha[:12],
            changed_files=stored,
            truncated=truncated,
            files_with_patch=sum(1 for f in files if f.has_patch),
        )

        # 5. Phase 2: deterministic structural analysis at this exact commit.
        #    Runs only after the head-SHA check above, so it can never analyse
        #    source belonging to a commit that a newer push has replaced.
        analysis = None
        if self._analyse:
            analysis = await self._run_analysis(
                owner=owner,
                repo_name=repo_name,
                installation_id=installation_id,
                repository_full_name=repository_full_name,
                pull_request_number=pull_request_number,
                head_sha=gh_pr.head.sha,
                files=files,
                truncated=truncated,
                log=log,
            )

        return SyncResult(
            repository_full_name=repository_full_name,
            pull_request_number=pull_request_number,
            head_sha=gh_pr.head.sha,
            changed_files=stored,
            truncated=truncated,
            analysis=analysis,
        )

    async def _run_analysis(
        self,
        *,
        owner: str,
        repo_name: str,
        installation_id: int,
        repository_full_name: str,
        pull_request_number: int,
        head_sha: str,
        files: list[GitHubChangedFile],
        truncated: bool,
        log: object,
    ) -> PullRequestAnalysis | None:
        """Run Phase 2 analysis. Never fails the synchronisation.

        A structural-analysis failure must not undo a successful metadata sync:
        the rows are already written and correct, and losing them to retry the
        parse would be a worse outcome than having no analysis this run.
        """
        provider = GitHubSourceProvider(
            self._client, installation_id=installation_id, owner=owner, repo=repo_name
        )
        pipeline = AnalysisPipeline(provider, config=self._analysis_config)
        inputs = [
            ChangedFileInput(
                path=f.filename,
                change_type=FileChangeType.from_github(f.status),
                patch=f.patch,
                previous_path=f.previous_filename,
                blob_sha=f.sha,
                additions=f.additions,
                deletions=f.deletions,
                is_binary=not f.has_patch,
            )
            for f in files
        ]
        try:
            analysis = await pipeline.analyse(
                repository_full_name=repository_full_name,
                pull_request_number=pull_request_number,
                head_sha=head_sha,
                changed_files=inputs,
                files_truncated_by_github=truncated,
            )
        # Deliberately broad: a structural-analysis failure must not undo a
        # successful metadata sync. The rows are already written and correct.
        except Exception as exc:
            logger.warning(
                "analysis.pull_request.failed",
                repository=repository_full_name,
                pull_request_number=pull_request_number,
                error_type=type(exc).__name__,
            )
            return None

        logger.info(
            "analysis.pull_request.ready",
            repository=repository_full_name,
            pull_request_number=pull_request_number,
            head_sha=head_sha[:12],
            files_analysed=analysis.analysed_files,
            files_skipped=analysis.skipped_files,
            declarations=analysis.declaration_total,
            changed_lines=analysis.changed_line_total,
        )
        return analysis
