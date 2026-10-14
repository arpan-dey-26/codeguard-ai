"""Source-file representation and per-file analysis status.

The invariant that matters most here, and the reason this module exists rather
than passing strings around:

    **A line's content never includes its line terminator, in any layer.**

The diff parser strips terminators when it splits a patch; ``SourceFile`` does
the same when it indexes a blob. Without that agreement, an anchor comparison
between a patch line and a file line silently fails on every CRLF file — the
patch line would lack the ``\\r`` and the file line would carry it.

Everything else is preserved exactly: tabs, trailing whitespace, Unicode,
zero-width characters, blank lines. Normalising any of those would make the
``anchor_snippet`` validation in Phase 3 unreliable, which is the one thing
this phase must not do. The undecoded bytes remain available on ``raw`` for
anyone who needs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceStatus(StrEnum):
    """Why a file does or does not have analysable source.

    Every value is a distinct, reportable situation. Collapsing them into a
    boolean is what produces the unanswerable support question "why wasn't my
    file reviewed?".
    """

    AVAILABLE = "available"
    DELETED = "deleted"
    """The PR removed the file, so no head version exists. Asking GitHub for
    one is a guaranteed 404, not an error to retry."""

    BINARY = "binary"
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    PR_BUDGET_EXCEEDED = "pr_budget_exceeded"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    VENDORED = "vendored"
    GENERATED = "generated"
    NO_PATCH = "no_patch"
    FETCH_FAILED = "fetch_failed"
    DECODE_FAILED = "decode_failed"

    @property
    def is_analysable(self) -> bool:
        return self is SourceStatus.AVAILABLE


class LineEnding(StrEnum):
    LF = "lf"
    CRLF = "crlf"
    MIXED = "mixed"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One file's content at one exact commit.

    ``head_sha`` is part of the identity, not metadata. A ``SourceFile`` whose
    commit is unknown cannot anchor anything, because "line 47 of session.py"
    is only meaningful relative to a snapshot.
    """

    path: str
    head_sha: str
    text: str
    raw: bytes = b""
    line_ending: LineEnding = LineEnding.LF
    had_final_newline: bool = True
    truncated: bool = False
    """True only when a limit forced a partial read.

    A truncated file's declarations describe a prefix of the file, never the
    whole of it, so nothing derived from it may be treated as authoritative for
    line validation (see ``FileAnalysis.is_authoritative``).
    """

    _lines: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @staticmethod
    def from_text(
        *,
        path: str,
        head_sha: str,
        text: str,
        raw: bytes = b"",
        truncated: bool = False,
    ) -> SourceFile:
        crlf = text.count("\r\n")
        bare_lf = text.count("\n") - crlf
        if crlf and bare_lf:
            ending = LineEnding.MIXED
        elif crlf:
            ending = LineEnding.CRLF
        elif bare_lf:
            ending = LineEnding.LF
        else:
            ending = LineEnding.NONE

        had_final_newline = text.endswith("\n")

        # Split on "\n" only. str.splitlines() also breaks on \x0b, \x0c,
        # \x1c-\x1e, \x85 and  , every one of which can appear inside a
        # source line — splitting on them would invent lines and shift every
        # number after them. Git splits on \n, so we split on \n.
        parts = text.split("\n")
        if parts and parts[-1] == "":
            parts.pop()  # the terminator, not a line
        # Drop the CR of a CRLF pair so a line's content matches the diff's.
        lines = tuple(p[:-1] if p.endswith("\r") else p for p in parts)

        return SourceFile(
            path=path,
            head_sha=head_sha,
            text=text,
            raw=raw,
            line_ending=ending,
            had_final_newline=had_final_newline,
            truncated=truncated,
            _lines=lines,
        )

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def byte_size(self) -> int:
        return len(self.raw) if self.raw else len(self.text.encode("utf-8"))

    def line(self, number: int) -> str | None:
        """Exact content of a 1-based line, without its terminator.

        ``None`` when the line does not exist, rather than an exception or an
        empty string: "line 5000 of a 40-line file" is a claim to reject, and a
        caller must be able to tell it apart from "line 12, which is blank".
        """
        if number < 1 or number > len(self._lines):
            return None
        return self._lines[number - 1]

    def lines_in(self, start: int, end: int) -> tuple[str, ...]:
        """Inclusive 1-based slice, clamped to the file."""
        if start > end:
            return ()
        lo = max(1, start)
        hi = min(len(self._lines), end)
        if lo > hi:
            return ()
        return self._lines[lo - 1 : hi]

    def contains_line(self, number: int) -> bool:
        return 1 <= number <= len(self._lines)


@dataclass(frozen=True, slots=True)
class FileClassification:
    """The deterministic verdict on whether a file is worth parsing.

    Separate from ``SourceStatus`` because classification happens *before*
    fetching: deciding not to download a 4 MB lockfile is the point.
    """

    path: str
    language: str | None
    status: SourceStatus
    reason: str | None = None

    @property
    def should_fetch(self) -> bool:
        return self.status is SourceStatus.AVAILABLE
