"""Unified-diff parser.

A hand-written state machine over the patch text. Pure: string in, data out,
no I/O, no globals, no clock. That is what makes it exhaustively testable with
golden fixtures — and it needs to be, because every AI finding in Phase 3 is
validated against what this module produces. A bug here is a comment on the
wrong line of someone's code.

Why hand-written rather than a library: the parse is a small state machine, we
need a custom output shape anyway, and — decisively — owning it means every
edge case has a test rather than an assumption about a dependency's behaviour
on ``\\ No newline at end of file``.

The patch text is untrusted input. It arrives from GitHub, describing a
third-party repository, and may be truncated, malformed, or adversarial. The
parser therefore never raises on bad input: it returns a ``FileDiff`` carrying
``PatchStatus.MALFORMED`` and a reason, so one bad file cannot abort a whole
pull request's analysis.
"""

from __future__ import annotations

import re

from app.analysis.diff.models import (
    ChangedLine,
    ChangeType,
    DiffLine,
    FileChangeType,
    FileDiff,
    Hunk,
    LineMap,
    PatchStatus,
    Side,
)

# @@ -old_start[,old_count] +new_start[,new_count] @@ optional section heading
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<heading>.*)$"
)

# "\ No newline at end of file" — and its localised variants, which is why the
# match is on the leading backslash rather than the English text.
_NO_NEWLINE_PREFIX = "\\"

MAX_HUNKS_PER_FILE = 10_000
MAX_LINES_PER_FILE = 500_000


class DiffParseError(Exception):
    """Internal signal. Never escapes ``parse_patch``."""


def parse_patch(
    patch: str | None,
    *,
    old_path: str | None,
    new_path: str | None,
    change_type: FileChangeType,
    is_binary: bool = False,
) -> FileDiff:
    """Parse one file's unified-diff patch into a ``FileDiff``.

    ``patch`` is ``None`` whenever GitHub omitted it. That is a classification,
    not an error, and the distinction between *binary*, *too large* and
    *unknown* is preserved rather than flattened — a caller that cannot tell
    them apart cannot explain to a user why their file was skipped.
    """
    if patch is None:
        status = (
            PatchStatus.ABSENT_BINARY
            if is_binary
            else PatchStatus.ABSENT_UNKNOWN
            if change_type is not FileChangeType.RENAMED
            else PatchStatus.EMPTY
        )
        return FileDiff(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
            patch_status=status,
        )

    if not patch.strip():
        # A pure rename or a mode change produces an empty patch. Empty is a
        # valid, complete answer — the file changed in a way that touched no
        # lines — and must not be reported as malformed.
        return FileDiff(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
            patch_status=PatchStatus.EMPTY,
        )

    try:
        hunks = _parse_hunks(patch)
    except DiffParseError as exc:
        return FileDiff(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
            patch_status=PatchStatus.MALFORMED,
            parse_error=str(exc)[:200],
        )

    if not hunks:
        return FileDiff(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
            patch_status=PatchStatus.MALFORMED,
            parse_error="No hunk header found in a non-empty patch.",
        )

    additions = sum(1 for h in hunks for ln in h.lines if ln.change_type is ChangeType.ADDED)
    deletions = sum(1 for h in hunks for ln in h.lines if ln.change_type is ChangeType.DELETED)
    return FileDiff(
        old_path=old_path,
        new_path=new_path,
        change_type=change_type,
        patch_status=PatchStatus.PRESENT,
        hunks=tuple(hunks),
        additions=additions,
        deletions=deletions,
    )


def _as_int(value: object) -> int:
    """Read a hunk-header field that the regex guarantees is numeric.

    The accumulator is ``dict[str, object]`` because it also holds the section
    heading, so every numeric read needs a cast. One checked accessor states
    the invariant — these five keys always hold digits, because the regex that
    wrote them only matches digits — instead of five suppressions that say
    nothing individually and would each have to be re-justified.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise DiffParseError(f"Hunk header field is not numeric: {value!r}")


def _parse_hunks(patch: str) -> list[Hunk]:
    """Walk the patch, maintaining two independent line cursors.

    The cursors are the whole algorithm:

        ' '  context   -> advance BOTH
        '+'  addition  -> advance NEW only
        '-'  deletion  -> advance OLD only
        '\\'  no-newline marker -> advance NEITHER

    The last rule is the classic off-by-one in diff parsers: the marker is
    metadata about the preceding line, not a line of its own, and advancing a
    cursor for it silently shifts every subsequent line number in the file.
    """
    # splitlines() would also split on \x0b, \x0c, \x1c-\x1e and  , which
    # can legitimately appear *inside* a source line. Splitting on those would
    # invent lines that do not exist and corrupt every number after them.
    normalised = patch.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = normalised.split("\n")

    # A patch that ends with a newline yields one trailing "" from split().
    # That empty string is the terminator, not a line. Treating it as content
    # invents a phantom context line at the end of every hunk and shifts the
    # line count by one — which is precisely the class of off-by-one this
    # module exists to avoid. A genuinely blank line inside a patch carries its
    # ' ' marker and so is never the empty string.
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()

    if len(raw_lines) > MAX_LINES_PER_FILE:
        raise DiffParseError(f"Patch exceeds {MAX_LINES_PER_FILE} lines.")

    hunks: list[Hunk] = []
    current: dict[str, object] | None = None
    lines: list[DiffLine] = []
    old_cursor = 0
    new_cursor = 0

    def close() -> None:
        nonlocal current, lines
        if current is not None:
            hunks.append(
                Hunk(
                    index=_as_int(current["index"]),
                    old_start=_as_int(current["old_start"]),
                    old_count=_as_int(current["old_count"]),
                    new_start=_as_int(current["new_start"]),
                    new_count=_as_int(current["new_count"]),
                    section_heading=str(current["heading"]),
                    lines=tuple(lines),
                )
            )
        current = None
        lines = []

    for raw in raw_lines:
        header = _HUNK_HEADER.match(raw)
        if header:
            close()
            if len(hunks) >= MAX_HUNKS_PER_FILE:
                raise DiffParseError(f"Patch exceeds {MAX_HUNKS_PER_FILE} hunks.")
            old_start = int(header.group("old_start"))
            new_start = int(header.group("new_start"))
            # An omitted count means 1; a count of 0 means the hunk covers an
            # empty range on that side (a pure addition into a new file, or a
            # deletion of everything).
            old_count = (
                int(header.group("old_count")) if header.group("old_count") is not None else 1
            )
            new_count = (
                int(header.group("new_count")) if header.group("new_count") is not None else 1
            )
            current = {
                "index": len(hunks),
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "heading": header.group("heading").strip(),
            }
            old_cursor = old_start
            new_cursor = new_start
            continue

        if current is None:
            # Preamble: "diff --git", "index ...", "--- a/x", "+++ b/x".
            # GitHub's per-file patch usually starts at the first @@, but the
            # full-diff form includes these, so they are skipped rather than
            # treated as content.
            continue

        if raw.startswith(_NO_NEWLINE_PREFIX):
            # Metadata about the previous line. Advances neither cursor.
            continue

        if raw == "":
            # A trailing empty string from the final split, or a genuinely
            # empty context line rendered without its leading space (some
            # generators do this). Treat it as context: it occupies a line in
            # both images, so both cursors must advance or everything after it
            # shifts by one.
            lines.append(
                DiffLine(
                    change_type=ChangeType.CONTEXT,
                    content="",
                    old_line=old_cursor,
                    new_line=new_cursor,
                )
            )
            old_cursor += 1
            new_cursor += 1
            continue

        marker, content = raw[0], raw[1:]
        if marker == "+":
            lines.append(
                DiffLine(change_type=ChangeType.ADDED, content=content, new_line=new_cursor)
            )
            new_cursor += 1
        elif marker == "-":
            lines.append(
                DiffLine(change_type=ChangeType.DELETED, content=content, old_line=old_cursor)
            )
            old_cursor += 1
        elif marker == " ":
            lines.append(
                DiffLine(
                    change_type=ChangeType.CONTEXT,
                    content=content,
                    old_line=old_cursor,
                    new_line=new_cursor,
                )
            )
            old_cursor += 1
            new_cursor += 1
        else:
            raise DiffParseError(f"Unrecognised diff line marker: {marker!r}")

    close()
    return hunks


def build_line_map(file_diff: FileDiff, *, head_sha: str) -> LineMap:
    """Project a parsed ``FileDiff`` into the queryable ``LineMap``.

    Separate from parsing on purpose. Parsing answers "what does this patch
    say?"; this answers "what may we do with it?". Keeping them apart means the
    commentability policy can change without touching the parser, and the
    parser's golden fixtures stay valid when it does.
    """
    changed: list[ChangedLine] = []
    added: set[int] = set()
    context: set[int] = set()
    deleted: set[int] = set()
    by_new: dict[int, ChangedLine] = {}
    by_old: dict[int, ChangedLine] = {}

    path = file_diff.path
    for hunk in file_diff.hunks:
        for line in hunk.lines:
            entry = ChangedLine(
                file_path=path,
                head_sha=head_sha,
                change_type=line.change_type,
                side=line.side,
                hunk_index=hunk.index,
                old_line=line.old_line,
                new_line=line.new_line,
                content=line.content,
            )
            changed.append(entry)
            if line.change_type is ChangeType.ADDED and line.new_line is not None:
                added.add(line.new_line)
                by_new[line.new_line] = entry
            elif line.change_type is ChangeType.CONTEXT and line.new_line is not None:
                context.add(line.new_line)
                # Added lines win a collision; they cannot legitimately occur,
                # but a malformed patch must not produce an ambiguous map.
                by_new.setdefault(line.new_line, entry)
            elif line.change_type is ChangeType.DELETED and line.old_line is not None:
                deleted.add(line.old_line)
                by_old[line.old_line] = entry

    changed.sort(key=ChangedLine.sort_key)

    return LineMap(
        file_path=path,
        head_sha=head_sha,
        old_path=file_diff.old_path,
        change_type=file_diff.change_type,
        patch_status=file_diff.patch_status,
        changed_lines=tuple(changed),
        added_new_lines=frozenset(added),
        context_new_lines=frozenset(context),
        deleted_old_lines=frozenset(deleted),
        hunk_count=len(file_diff.hunks),
        parse_error=file_diff.parse_error,
        _by_new_line=by_new,
        _by_old_line=by_old,
    )


__all__ = [
    "DiffParseError",
    "Side",
    "build_line_map",
    "parse_patch",
]
