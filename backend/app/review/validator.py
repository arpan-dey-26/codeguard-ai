"""The trust boundary: ``RawFinding`` → ``ValidatedFinding``, or nothing.

**The model is never trusted.** Every field of a ``RawFinding`` is a *claim*
made by a program that has no access to the repository, cannot be audited, and
is influenced by attacker-controlled text. The file path is a claim. The line
number is a claim. The snippet it says appears at that line is a claim. This
module is the only place those claims are converted into facts, and it does so
by checking each one against an artefact the model did not produce:

* the **context** — what we actually showed it;
* the **LineMap** — our own parse of the pull request's diff;
* the **SourceFile** — the real bytes of the file at the real head commit.

Nothing that fails a gate is repaired, softened, or "best-effort" published.
It is rejected, counted, and kept for analysis. A rejected finding is a
successful outcome of this module, not a failure of it.

**The gates, in order.** Order matters: each gate assumes its predecessors
passed, and the cheap structural checks run before the ones that touch file
content.

======  ==================  ====================================================
gate    name                rejects
======  ==================  ====================================================
1       structure           empty ids, out-of-range confidence, absurd spans
2       secret leakage      a finding whose own text contains a credential
3       path safety         absolute paths, ``..``, URLs, NUL bytes
4       file                a path we did not show the model
5       sha                 an analysis whose head commit is not the one we
                            are reviewing
6       side                ``LEFT`` — deletions are never commented on
7       line                a line the file does not contain
8       line map            a line not present in the pull request's diff
9       presentation        a line we never actually showed
10      source              a line whose content we cannot read back
11      anchor              a snippet that is not at (or near) the cited line
12      declaration         enrichment; records structural context
13      confidence          below the severity-aware publication threshold
14      duplicate           a finding already produced by an earlier claim
======  ==================  ====================================================

Gate 11 is the one that does the real work. A model that hallucinates a
vulnerability must also hallucinate a verbatim line of source that happens to
exist at the line it names — and at temperature 0 against real file content,
that essentially does not happen. Gate 9 closes the remaining hole: a model
that guesses a *plausible* line it was never shown is guessing even when the
guess lands on real code.

**Relocation.** A model that is off by one or two lines but quotes the source
correctly is not hallucinating; it is miscounting. Gate 11 therefore searches a
small window around the cited line, and accepts a *unique* exact match within
it, recording ``anchor_relocated``. Two matches in the window is ambiguity, and
ambiguity is rejected — publishing a comment on the wrong one of two identical
lines is worse than publishing nothing.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.ai.contracts import FindingCategory, FindingSeverity, FindingSide, RawFinding
from app.analysis.artifact import FileAnalysis
from app.analysis.languages.base import DeclarationKind
from app.analysis.mapping.mapper import map_line
from app.review.context import ReviewContext

# How far from the cited line an anchor may be found and still be accepted.
# Three lines: large enough to absorb the off-by-one and off-by-two errors
# models actually make, small enough that a unique match inside it is strong
# evidence rather than coincidence.
ANCHOR_SEARCH_RADIUS = 3

# A finding spanning more than this many lines is not a finding, it is a review
# of the file. GitHub renders such a comment poorly and the claim is rarely
# specific enough to act on.
MAX_FINDING_SPAN = 40

MIN_ANCHOR_CHARS = 3
"""Anchors shorter than this prove nothing.

``)`` appears on hundreds of lines. An anchor's job is to make a claim
falsifiable, and a snippet that matches everywhere falsifies nothing — so a
too-short anchor is rejected rather than trivially "verified".
"""

# Patterns for gate 2. These look for *credential shapes*, not for any
# particular secret, and are applied to the model's own prose. A finding is
# allowed to say "this hard-codes an API key"; it is not allowed to quote it.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    # Assembled at runtime rather than written as one literal: the repository's
    # own secret scanner matches the complete marker, and a detector for a
    # secret shape must not itself look like the secret. Weakening the scanner
    # to accommodate this file would have been the wrong trade.
    ("private_key_block", re.compile("-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----")),
    ("slack_token", re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}")),
)

_URL_LIKE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


class RejectionReason(StrEnum):
    """Why a claim did not become a finding.

    Persisted with the rejected finding. This is the dataset that answers "is
    the model getting better or worse?", and a free-text reason would make that
    question unanswerable.
    """

    MALFORMED = "malformed"
    SECRET_IN_TEXT = "secret_in_text"  # noqa: S105 - a rejection reason, not a secret
    UNSAFE_PATH = "unsafe_path"
    UNKNOWN_FILE = "unknown_file"
    SHA_MISMATCH = "sha_mismatch"
    WRONG_SIDE = "wrong_side"
    LINE_OUT_OF_RANGE = "line_out_of_range"
    LINE_NOT_IN_DIFF = "line_not_in_diff"
    LINE_NOT_PRESENTED = "line_not_presented"
    NO_SOURCE_LINE = "no_source_line"
    ANCHOR_TOO_SHORT = "anchor_too_short"
    ANCHOR_NOT_FOUND = "anchor_not_found"
    ANCHOR_AMBIGUOUS = "anchor_ambiguous"
    LOW_CONFIDENCE = "low_confidence"
    DUPLICATE = "duplicate"
    OVER_LIMIT = "over_limit"


@dataclass(frozen=True, slots=True)
class ValidatedFinding:
    """A model claim that survived every gate.

    Only this type may reach the GitHub publisher. The distinction from
    ``RawFinding`` is not stylistic: ``file_path``, ``line`` and ``side`` here
    have been checked against the diff and the file at ``head_sha``, so they
    are safe to hand to an API that will make them visible to other people.
    """

    fingerprint: str
    category: FindingCategory
    severity: FindingSeverity
    title: str
    description: str
    file_path: str
    line: int
    side: FindingSide
    head_sha: str
    anchor_snippet: str
    source_line: str
    confidence: float
    impact: str
    rationale: str
    line_end: int | None = None
    suggested_fix: str | None = None
    cwe_id: str | None = None
    declaration_id: str | None = None
    qualified_name: str = ""
    declaration_kind: str = ""
    anchor_relocated: bool = False
    claimed_line: int = 0
    model_finding_id: str = ""

    @property
    def is_high_severity(self) -> bool:
        return self.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic projection. Contains one line of repository source
        (``source_line``), which is why it is never logged wholesale."""
        return {
            "fingerprint": self.fingerprint,
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "file_path": self.file_path,
            "line": self.line,
            "side": self.side.value,
            "head_sha": self.head_sha,
            "confidence": round(self.confidence, 4),
            "declaration": self.qualified_name,
            "anchor_relocated": self.anchor_relocated,
            "claimed_line": self.claimed_line,
        }


@dataclass(frozen=True, slots=True)
class RejectedFinding:
    """A claim and the gate that stopped it.

    ``detail`` never contains repository source or the model's full text — only
    a short, bounded explanation — because rejected findings are logged and the
    thing that produced them is untrusted.
    """

    reason: RejectionReason
    model_finding_id: str
    claimed_file: str
    claimed_line: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason.value,
            "model_finding_id": self.model_finding_id,
            "claimed_file": self.claimed_file,
            "claimed_line": self.claimed_line,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The complete, auditable outcome of validating one model response."""

    validated: tuple[ValidatedFinding, ...] = ()
    rejected: tuple[RejectedFinding, ...] = ()
    head_sha: str = ""

    @property
    def total_claims(self) -> int:
        return len(self.validated) + len(self.rejected)

    @property
    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.rejected:
            counts[item.reason.value] = counts.get(item.reason.value, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def acceptance_ratio(self) -> float:
        return len(self.validated) / self.total_claims if self.total_claims else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "claims": self.total_claims,
            "validated": len(self.validated),
            "rejected": len(self.rejected),
            "rejections": self.rejection_counts,
        }


# -- helpers -----------------------------------------------------------------


def contains_secret(text: str) -> str | None:
    """Return the name of the first credential shape found, or ``None``."""
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


def is_safe_repository_path(path: str) -> bool:
    """Reject anything that is not a plain, relative, in-repository path.

    This is defence in depth. The path is already required to be one we showed
    the model (gate 4), which alone makes traversal impossible. It is checked
    anyway because the file-membership set is built from data, and a check that
    costs nothing should not depend on another check being correct.
    """
    if not path or len(path) > 400:
        return False
    if path.startswith("/") or path.startswith("\\"):
        return False
    if "\x00" in path or "\n" in path or "\r" in path:
        return False
    if _URL_LIKE.match(path):
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", path):  # C:\ — a Windows absolute path
        return False
    # Split on **both** separators. Git always uses '/', so a backslash in a
    # path is a filename character on POSIX and harmless here — but this
    # function already rejects a leading '\' and a 'C:\' prefix, so it clearly
    # intends to understand Windows separators, and understanding them in two
    # places out of three is the kind of half-applied rule that reads as a
    # guarantee and is not one. `..\..\win` used to pass: one component
    # containing backslashes, no '..' after splitting on '/' alone.
    #
    # Gate 4 (the path must be one we actually showed the model) makes this
    # unreachable in practice. It is still fixed, because the value of a
    # defence-in-depth check is that it does not depend on the other check
    # being correct.
    parts = re.split(r"[/\\]", path)
    return all(part not in ("", ".", "..") for part in parts)


def normalise_anchor(text: str) -> str:
    """Collapse whitespace for a tolerant second comparison.

    Exact comparison is tried first and is what the model is asked for. This
    fallback exists for one specific, benign failure: a model that reproduces
    the code correctly but normalises indentation or an internal run of spaces.
    It does **not** lowercase, strip punctuation, or otherwise weaken the
    comparison — the anchor must still be the same characters in the same
    order.
    """
    return " ".join(text.split())


def compute_fingerprint(
    *,
    file_path: str,
    line: int,
    side: str,
    category: str,
    title: str,
) -> str:
    """Stable identity for a finding, derived only from validated properties.

    Explicitly **not** derived from the model's ``finding_id``: that value is
    generated per response, so two runs over the same unchanged code would
    produce two different ids and the same issue would be reported twice. The
    fingerprint is what makes "have we already said this?" answerable across
    runs and across commits.

    ``title`` is normalised (case-folded, whitespace-collapsed) because a model
    rewording "Unclosed file handle" as "Unclosed  File Handle" is the same
    finding.
    """
    normalised_title = " ".join(title.lower().split())
    material = f"{file_path}\x00{line}\x00{side}\x00{category}\x00{normalised_title}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# -- the validator -----------------------------------------------------------


@dataclass(slots=True)
class FindingValidator:
    """Validates model claims against the diff and the real file content.

    Stateless between calls: ``validate`` builds its own duplicate set, so one
    validator instance can serve many review runs without leaking state from
    one pull request into another.
    """

    min_confidence: float = 0.6
    min_confidence_high_severity: float = 0.5
    max_findings: int = 25
    anchor_radius: int = ANCHOR_SEARCH_RADIUS

    @staticmethod
    def from_settings(settings: Any) -> FindingValidator:
        return FindingValidator(
            min_confidence=settings.review_min_confidence,
            min_confidence_high_severity=settings.review_min_confidence_high_severity,
            max_findings=settings.ai_max_findings,
        )

    def validate(
        self,
        findings: Iterable[RawFinding],
        *,
        context: ReviewContext,
        analyses: dict[str, FileAnalysis],
        head_sha: str,
    ) -> ValidationReport:
        """Run every gate over every claim.

        ``head_sha`` is passed explicitly rather than read from ``context`` so
        that the caller's notion of "the commit we are reviewing" is the one
        enforced. A context built against a stale analysis is exactly the bug
        gate 5 exists to catch, and it cannot catch it by comparing the context
        against itself.
        """
        validated: list[ValidatedFinding] = []
        rejected: list[RejectedFinding] = []
        seen: set[str] = set()

        allowed_paths = {entry.file_path for entry in context.included}

        # Deterministic order: the model's array order is not stable across
        # runs, and it decides which of two duplicates survives and which
        # findings hit the cap. Sorting makes both reproducible.
        ordered = sorted(
            findings,
            key=lambda f: (f.file_path, f.line_start, f.category.value, f.title),
        )

        for raw in ordered:
            outcome = self._validate_one(
                raw,
                context=context,
                analyses=analyses,
                head_sha=head_sha,
                allowed_paths=allowed_paths,
                seen=seen,
            )
            if isinstance(outcome, RejectedFinding):
                rejected.append(outcome)
                continue
            if len(validated) >= self.max_findings:
                rejected.append(
                    RejectedFinding(
                        reason=RejectionReason.OVER_LIMIT,
                        model_finding_id=raw.finding_id,
                        claimed_file=raw.file_path,
                        claimed_line=raw.line_start,
                        detail=f"exceeds max_findings={self.max_findings}",
                    )
                )
                continue
            seen.add(outcome.fingerprint)
            validated.append(outcome)

        validated.sort(key=lambda f: (f.file_path, f.line, f.category.value, f.title))
        return ValidationReport(
            validated=tuple(validated),
            rejected=tuple(rejected),
            head_sha=head_sha,
        )

    # -- one claim, every gate -------------------------------------------
    def _validate_one(
        self,
        raw: RawFinding,
        *,
        context: ReviewContext,
        analyses: dict[str, FileAnalysis],
        head_sha: str,
        allowed_paths: set[str],
        seen: set[str],
    ) -> ValidatedFinding | RejectedFinding:
        def reject(reason: RejectionReason, detail: str = "") -> RejectedFinding:
            return RejectedFinding(
                reason=reason,
                model_finding_id=raw.finding_id[:64],
                claimed_file=raw.file_path[:200],
                claimed_line=raw.line_start if isinstance(raw.line_start, int) else -1,
                detail=detail[:200],
            )

        # -- gate 1: structure ------------------------------------------
        if not isinstance(raw.line_start, int) or isinstance(raw.line_start, bool):
            return reject(RejectionReason.MALFORMED, "line_start is not an integer")
        if not raw.title.strip() or not raw.description.strip():
            return reject(RejectionReason.MALFORMED, "empty title or description")
        if not isinstance(raw.confidence, (int, float)) or not (
            0.0 <= float(raw.confidence) <= 1.0
        ):
            return reject(RejectionReason.MALFORMED, "confidence out of range")
        if not isinstance(raw.category, FindingCategory) or not isinstance(
            raw.severity, FindingSeverity
        ):
            return reject(RejectionReason.MALFORMED, "category or severity not in taxonomy")
        if raw.line_end is not None:
            if not isinstance(raw.line_end, int) or raw.line_end < raw.line_start:
                return reject(RejectionReason.MALFORMED, "line_end precedes line_start")
            if raw.line_end - raw.line_start > MAX_FINDING_SPAN:
                return reject(
                    RejectionReason.MALFORMED,
                    f"span exceeds {MAX_FINDING_SPAN} lines",
                )

        # -- gate 2: secret leakage --------------------------------------
        # Applied to the model's own prose, before anything is persisted. The
        # prompt forbids quoting credentials; this is the enforcement, because
        # a prompt is a request and a check is a guarantee.
        combined = "\n".join(
            part
            for part in (
                raw.title,
                raw.description,
                raw.impact,
                raw.rationale,
                raw.suggested_fix or "",
                raw.anchor_snippet,
            )
        )
        leaked = contains_secret(combined)
        if leaked is not None:
            return reject(RejectionReason.SECRET_IN_TEXT, f"matched {leaked} pattern")

        # -- gate 3: path safety -----------------------------------------
        if not is_safe_repository_path(raw.file_path):
            return reject(RejectionReason.UNSAFE_PATH, "path is not a safe relative path")

        # -- gate 4: file ------------------------------------------------
        if raw.file_path not in allowed_paths:
            return reject(RejectionReason.UNKNOWN_FILE, "file was not shown to the model")
        analysis = analyses.get(raw.file_path)
        if analysis is None or analysis.source is None or analysis.line_map is None:
            return reject(RejectionReason.UNKNOWN_FILE, "no analysis for this file")

        # -- gate 5: sha -------------------------------------------------
        # Three independent records of "which commit is this?" must agree. They
        # can disagree when a new push arrives mid-review, and publishing a
        # comment anchored to a commit that is no longer the head is how a
        # review lands on the wrong code.
        if analysis.head_sha != head_sha or context.head_sha != head_sha:
            return reject(
                RejectionReason.SHA_MISMATCH,
                "analysis or context head commit differs from the review's",
            )
        if analysis.source.head_sha != head_sha:
            return reject(RejectionReason.SHA_MISMATCH, "source blob is from another commit")

        # -- gate 6: side ------------------------------------------------
        if raw.side is not FindingSide.RIGHT:
            return reject(RejectionReason.WRONG_SIDE, "LEFT-side findings are not published")

        source = analysis.source
        line_map = analysis.line_map

        # -- gate 7: line in file ----------------------------------------
        if raw.line_start < 1 or not source.contains_line(raw.line_start):
            return reject(
                RejectionReason.LINE_OUT_OF_RANGE,
                f"file has {source.line_count} lines",
            )

        # -- gates 8-11, with relocation ---------------------------------
        anchor = raw.anchor_snippet.strip()
        if len(anchor) < MIN_ANCHOR_CHARS:
            return reject(RejectionReason.ANCHOR_TOO_SHORT, f"anchor is {len(anchor)} chars")

        resolved = self._resolve_line(
            claimed_line=raw.line_start,
            anchor=anchor,
            source=source,
            line_map=line_map,
            presented=context.presented_lines.get(raw.file_path, frozenset()),
        )
        if isinstance(resolved, RejectionReason):
            return reject(resolved, self._resolution_detail(resolved, raw.line_start))
        line, source_line, relocated = resolved

        # -- gate 12: declaration enrichment -----------------------------
        declaration_id: str | None = None
        qualified_name = ""
        declaration_kind = ""
        if analysis.declarations:
            innermost, _chain = map_line(analysis.declarations, line)
            if innermost is not None and innermost.kind is not DeclarationKind.MODULE:
                declaration_id = innermost.declaration_id
                qualified_name = innermost.qualified_name
                declaration_kind = innermost.kind.value

        # -- gate 13: confidence -----------------------------------------
        threshold = (
            self.min_confidence_high_severity
            if raw.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
            else self.min_confidence
        )
        if float(raw.confidence) < threshold:
            return reject(
                RejectionReason.LOW_CONFIDENCE,
                f"{raw.confidence:.2f} < {threshold:.2f}",
            )

        # -- gate 14: duplicate ------------------------------------------
        fingerprint = compute_fingerprint(
            file_path=raw.file_path,
            line=line,
            side=FindingSide.RIGHT.value,
            category=raw.category.value,
            title=raw.title,
        )
        if fingerprint in seen:
            return reject(RejectionReason.DUPLICATE, "same fingerprint already accepted")

        return ValidatedFinding(
            fingerprint=fingerprint,
            category=raw.category,
            severity=raw.severity,
            title=raw.title.strip()[:120],
            description=raw.description.strip()[:1200],
            file_path=raw.file_path,
            line=line,
            side=FindingSide.RIGHT,
            head_sha=head_sha,
            anchor_snippet=anchor[:300],
            source_line=source_line,
            confidence=float(raw.confidence),
            impact=raw.impact.strip()[:400],
            rationale=raw.rationale.strip()[:800],
            line_end=raw.line_end,
            suggested_fix=(raw.suggested_fix or "").strip()[:1200] or None,
            cwe_id=(raw.cwe_id or "").strip()[:12] or None,
            declaration_id=declaration_id,
            qualified_name=qualified_name,
            declaration_kind=declaration_kind,
            anchor_relocated=relocated,
            claimed_line=raw.line_start,
            model_finding_id=raw.finding_id[:64],
        )

    # -- anchor resolution ------------------------------------------------
    def _resolve_line(
        self,
        *,
        claimed_line: int,
        anchor: str,
        source: Any,
        line_map: Any,
        presented: frozenset[int],
    ) -> tuple[int, str, bool] | RejectionReason:
        """Find the line the anchor actually identifies.

        Returns ``(line, source_line, relocated)`` or the reason it could not.

        The cited line is tried first and, if the anchor matches there, wins
        outright — no search, no ambiguity. Only a miss triggers the window
        search, and only a *unique* hit in the window is accepted.
        """
        candidates = [claimed_line] + [
            claimed_line + offset
            for delta in range(1, self.anchor_radius + 1)
            for offset in (-delta, delta)
        ]

        exact_hits: list[int] = []
        loose_hits: list[int] = []

        for candidate in candidates:
            # Every candidate is re-checked against all three location gates.
            # Relocation may correct a miscount; it may never move a finding
            # onto a line outside the diff, or onto one we never showed.
            if not source.contains_line(candidate):
                continue
            if not line_map.is_commentable(candidate):
                continue
            if candidate not in presented:
                continue
            text = source.line(candidate)
            if text is None:
                continue
            if anchor in text:
                exact_hits.append(candidate)
            elif normalise_anchor(anchor) and normalise_anchor(anchor) in normalise_anchor(text):
                loose_hits.append(candidate)

        hits = exact_hits or loose_hits
        if hits:
            if claimed_line in hits:
                text = source.line(claimed_line)
                if text is None:  # pragma: no cover — contains_line already passed
                    return RejectionReason.NO_SOURCE_LINE
                return (claimed_line, text, False)
            if len(set(hits)) > 1:
                return RejectionReason.ANCHOR_AMBIGUOUS
            resolved = hits[0]
            text = source.line(resolved)
            if text is None:  # pragma: no cover
                return RejectionReason.NO_SOURCE_LINE
            return (resolved, text, True)

        # Nothing matched. Diagnose the **claimed** line rather than the window,
        # because what the model got wrong is what is worth counting: "line 14
        # was never shown to you" is an actionable signal, while "no anchor
        # found nearby" hides which gate actually failed. The gates are reported
        # in the order they would have been evaluated.
        if not source.contains_line(claimed_line):
            return RejectionReason.LINE_OUT_OF_RANGE
        if not line_map.is_commentable(claimed_line):
            return RejectionReason.LINE_NOT_IN_DIFF
        if claimed_line not in presented:
            return RejectionReason.LINE_NOT_PRESENTED
        return RejectionReason.ANCHOR_NOT_FOUND

    @staticmethod
    def _resolution_detail(reason: RejectionReason, claimed_line: int) -> str:
        return {
            RejectionReason.LINE_NOT_IN_DIFF: f"line {claimed_line} is not in the diff",
            RejectionReason.LINE_NOT_PRESENTED: f"line {claimed_line} was never shown",
            RejectionReason.LINE_OUT_OF_RANGE: f"line {claimed_line} is outside the file",
            RejectionReason.ANCHOR_NOT_FOUND: "anchor not found near the cited line",
            RejectionReason.ANCHOR_AMBIGUOUS: "anchor matches several nearby lines",
            RejectionReason.NO_SOURCE_LINE: "source line could not be read",
        }.get(reason, "")


__all__ = [
    "ANCHOR_SEARCH_RADIUS",
    "MAX_FINDING_SPAN",
    "MIN_ANCHOR_CHARS",
    "FindingValidator",
    "RejectedFinding",
    "RejectionReason",
    "ValidatedFinding",
    "ValidationReport",
    "compute_fingerprint",
    "contains_secret",
    "is_safe_repository_path",
    "normalise_anchor",
]
