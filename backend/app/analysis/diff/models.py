"""Domain types for unified diffs.

Pure data. No GitHub, no database, no I/O — which is what makes the diff layer
testable in isolation and reusable if the platform ever speaks to something
other than GitHub.

The vocabulary here is deliberately precise, because the whole trust plane in
Phase 3 will be expressed in it:

``old_line``   1-based line number in the **pre-image** (the base file).
``new_line``   1-based line number in the **post-image** (the head file).
``side``       ``LEFT`` = pre-image, ``RIGHT`` = post-image. GitHub's review
               API uses exactly these words, so we do too rather than
               inventing a synonym that has to be translated at the boundary.
``change_type`` what the diff did to this line.

A line has an ``old_line`` **or** a ``new_line`` **or both**:

    added    -> new_line only      (RIGHT)
    deleted  -> old_line only      (LEFT)
    context  -> both               (RIGHT, unchanged text)

Deletions are kept as first-class records rather than being folded into the
new file. GitHub's inline-comment semantics for a deleted line are genuinely
different (``side: LEFT``), and quietly treating a deletion as an ordinary
new-file line is how a review comment ends up on the wrong code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Side(StrEnum):
    """Which image of the file a line belongs to."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"


class ChangeType(StrEnum):
    """What the diff did to a single line."""

    ADDED = "added"
    DELETED = "deleted"
    CONTEXT = "context"


class FileChangeType(StrEnum):
    """What the pull request did to a whole file.

    Mirrors GitHub's documented vocabulary for a file's ``status`` so that no
    translation table is needed at the boundary. ``UNSUPPORTED`` is ours: it
    marks a status GitHub sent that we do not model, which is recorded rather
    than guessed at.
    """

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "removed"
    RENAMED = "renamed"
    COPIED = "copied"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    UNSUPPORTED = "unsupported"

    @classmethod
    def from_github(cls, status: str) -> FileChangeType:
        try:
            return cls(status)
        except ValueError:
            return cls.UNSUPPORTED

    @property
    def has_head_version(self) -> bool:
        """Whether a post-image of this file exists at the head commit.

        ``False`` for a deletion — attempting to fetch head content for a file
        the PR removed is a guaranteed 404, and asking is a bug rather than an
        error to handle.
        """
        return self is not FileChangeType.DELETED


@dataclass(frozen=True, slots=True, order=True)
class DiffLine:
    """One line inside a hunk.

    ``content`` is the line's text with the diff's leading marker removed and
    **no other normalisation**: no strip, no dedent, no newline handling. It is
    a faithful copy of what the patch carried, so that anything derived from it
    can be compared against real file content later.

    ``content`` from a patch is nevertheless *not* the anchor of record. The
    authoritative text comes from the head blob (see ``SourceFile``); patch
    text can be truncated by GitHub and is unavailable for some files.
    """

    change_type: ChangeType
    content: str
    old_line: int | None = None
    new_line: int | None = None

    @property
    def side(self) -> Side:
        return Side.LEFT if self.change_type is ChangeType.DELETED else Side.RIGHT


@dataclass(frozen=True, slots=True)
class Hunk:
    """One ``@@`` section of a file's patch.

    ``section_heading`` is the text git puts after the closing ``@@``. It is a
    *heuristic hint* produced by git's hunk-header matcher, not parsed code,
    and nothing downstream may treat it as a declaration name.
    """

    index: int
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section_heading: str = ""
    lines: tuple[DiffLine, ...] = ()

    @property
    def old_end(self) -> int:
        """Last pre-image line covered, inclusive. Equals ``old_start - 1``
        when the hunk adds into an empty range (``old_count == 0``)."""
        return self.old_start + self.old_count - 1

    @property
    def new_end(self) -> int:
        return self.new_start + self.new_count - 1

    def contains_new_line(self, line: int) -> bool:
        return self.new_count > 0 and self.new_start <= line <= self.new_end

    def contains_old_line(self, line: int) -> bool:
        return self.old_count > 0 and self.old_start <= line <= self.old_end


class PatchStatus(StrEnum):
    """Why a file does or does not have a usable patch.

    A missing patch must never collapse into "no changes" — that is the
    failure mode the whole enum exists to prevent. GitHub omits the patch for
    binary files and for diffs it considers too large, and those are different
    situations with different downstream handling.
    """

    PRESENT = "present"
    ABSENT_BINARY = "absent_binary"
    ABSENT_TOO_LARGE = "absent_too_large"
    ABSENT_UNKNOWN = "absent_unknown"
    EMPTY = "empty"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class FileDiff:
    """The parsed diff for a single file.

    ``old_path`` and ``new_path`` are both retained. For a rename they differ;
    for a deletion ``new_path`` is ``None``; for an addition ``old_path`` is
    ``None``. Collapsing them into one "path" loses the information a rename
    needs, and renames are common enough in real pull requests that losing it
    matters.
    """

    old_path: str | None
    new_path: str | None
    change_type: FileChangeType
    patch_status: PatchStatus
    hunks: tuple[Hunk, ...] = ()
    additions: int = 0
    deletions: int = 0
    parse_error: str | None = None

    @property
    def path(self) -> str:
        """The path to use for identity and for comment placement.

        The post-image path when one exists, otherwise the pre-image path, so
        a deleted file still has a stable identifier.
        """
        return self.new_path or self.old_path or ""

    @property
    def is_rename(self) -> bool:
        return (
            self.change_type is FileChangeType.RENAMED
            and self.old_path is not None
            and self.new_path is not None
            and self.old_path != self.new_path
        )

    @property
    def has_usable_patch(self) -> bool:
        return self.patch_status is PatchStatus.PRESENT and bool(self.hunks)

    @property
    def all_lines(self) -> tuple[DiffLine, ...]:
        return tuple(line for hunk in self.hunks for line in hunk.lines)


@dataclass(frozen=True, slots=True)
class ChangedLine:
    """A line the pull request touched, with everything needed to locate it.

    This is the unit the rest of the platform reasons about. It carries its own
    file path and head SHA so that a changed line is never interpretable
    without knowing which snapshot it came from — the mistake that makes a
    review comment land on the wrong commit.
    """

    file_path: str
    head_sha: str
    change_type: ChangeType
    side: Side
    hunk_index: int
    old_line: int | None = None
    new_line: int | None = None
    content: str = ""

    @property
    def is_commentable(self) -> bool:
        """Whether GitHub will accept an inline review comment here.

        Only post-image lines inside a hunk qualify. Deletions are excluded by
        policy, not by GitHub's capability: ``side: LEFT`` comments are
        accepted by the API but are almost always confusing, and excluding them
        removes an entire class of 422 without losing much
        (architecture.md §9.5).
        """
        return self.side is Side.RIGHT and self.new_line is not None

    def sort_key(self) -> tuple[str, int, int, str]:
        """Total order, so serialised output is byte-stable across runs."""
        return (
            self.file_path,
            self.new_line if self.new_line is not None else -1,
            self.old_line if self.old_line is not None else -1,
            self.change_type.value,
        )


@dataclass(frozen=True, slots=True)
class LineMap:
    """The authoritative answer to "what did this pull request change?".

    Built once per file per head SHA, from the diff alone, with no AI and no
    network. Everything the Phase 3 trust plane validates against is derived
    from here, which is why this type is immutable, deterministic, and has no
    dependency on anything above the diff layer.
    """

    file_path: str
    head_sha: str
    old_path: str | None
    change_type: FileChangeType
    patch_status: PatchStatus
    changed_lines: tuple[ChangedLine, ...] = ()
    added_new_lines: frozenset[int] = frozenset()
    context_new_lines: frozenset[int] = frozenset()
    deleted_old_lines: frozenset[int] = frozenset()
    hunk_count: int = 0
    parse_error: str | None = None
    _by_new_line: dict[int, ChangedLine] = field(default_factory=dict, repr=False, compare=False)
    _by_old_line: dict[int, ChangedLine] = field(default_factory=dict, repr=False, compare=False)

    # -- the questions a LineMap exists to answer --------------------------
    @property
    def commentable_new_lines(self) -> frozenset[int]:
        """Post-image lines where an inline comment may be placed.

        Added lines **and** context lines: GitHub accepts a comment on any line
        present in the diff, and a finding about an unchanged line that the
        change made wrong is a legitimate finding.
        """
        return self.added_new_lines | self.context_new_lines

    def is_changed(self, new_line: int) -> bool:
        """Did the pull request add or modify this post-image line?"""
        return new_line in self.added_new_lines

    def is_commentable(self, new_line: int) -> bool:
        return new_line in self.commentable_new_lines

    def is_deleted_old_line(self, old_line: int) -> bool:
        return old_line in self.deleted_old_lines

    def line_at(self, new_line: int) -> ChangedLine | None:
        return self._by_new_line.get(new_line)

    def deleted_line_at(self, old_line: int) -> ChangedLine | None:
        return self._by_old_line.get(old_line)

    def hunk_index_for_new_line(self, new_line: int) -> int | None:
        entry = self._by_new_line.get(new_line)
        return entry.hunk_index if entry else None

    @property
    def changed_new_lines_sorted(self) -> tuple[int, ...]:
        return tuple(sorted(self.added_new_lines))

    @property
    def is_empty(self) -> bool:
        return not self.changed_lines
