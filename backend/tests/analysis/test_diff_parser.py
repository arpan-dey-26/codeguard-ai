"""Golden fixtures for the unified-diff parser and the LineMap.

The most heavily tested module in the project, because it is the ground truth
every later AI finding is validated against. A bug here puts a review comment
on the wrong line of someone's code.

No GitHub, no database, no network: string in, data out.
"""

from __future__ import annotations

import pytest
from app.analysis.diff import (
    ChangeType,
    FileChangeType,
    PatchStatus,
    Side,
    build_line_map,
    parse_patch,
)

HEAD = "a" * 40


def parse(patch: str | None, change_type: FileChangeType = FileChangeType.MODIFIED, **kw):  # type: ignore[no-untyped-def]
    fd = parse_patch(
        patch,
        old_path=kw.pop("old_path", "f.py"),
        new_path=kw.pop("new_path", "f.py"),
        change_type=change_type,
        **kw,
    )
    return fd, build_line_map(fd, head_sha=HEAD)


# -- basic arithmetic -----------------------------------------------------


def test_single_added_line() -> None:
    _, lm = parse("@@ -1,2 +1,3 @@\n ctx\n+added\n ctx2\n")
    assert sorted(lm.added_new_lines) == [2]
    assert lm.line_at(2).content == "added"
    assert lm.line_at(2).side is Side.RIGHT


def test_single_deleted_line() -> None:
    fd, lm = parse("@@ -1,3 +1,2 @@\n ctx\n-gone\n ctx2\n")
    assert sorted(lm.deleted_old_lines) == [2]
    assert not lm.added_new_lines
    assert fd.deletions == 1
    deleted = lm.deleted_line_at(2)
    assert deleted.side is Side.LEFT
    assert deleted.new_line is None


def test_modified_line_is_a_delete_plus_an_add() -> None:
    """A modification is not a distinct diff concept; it is -old +new."""
    _, lm = parse("@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n")
    assert sorted(lm.added_new_lines) == [2]
    assert sorted(lm.deleted_old_lines) == [2]
    assert lm.line_at(2).content == "new"
    assert lm.deleted_line_at(2).content == "old"


def test_mixed_hunk_line_arithmetic() -> None:
    _, lm = parse(
        "@@ -41,7 +41,9 @@ def create_session(user_id):\n"
        "     token = secrets.token_urlsafe(32)\n"
        "-    expires = datetime.now() + timedelta(days=30)\n"
        "+    expires = datetime.now(timezone.utc) + timedelta(hours=12)\n"
        "+    if not user_id:\n"
        "+        raise ValueError('x')\n"
        "     store.put(token)\n"
        "     return Session(token)\n"
    )
    assert sorted(lm.added_new_lines) == [42, 43, 44]
    assert sorted(lm.context_new_lines) == [41, 45, 46]
    assert sorted(lm.deleted_old_lines) == [42]


def test_multiple_hunks_keep_independent_cursors() -> None:
    _, lm = parse("@@ -1,2 +1,3 @@\n ctx\n+add1\n ctx2\n@@ -50,2 +51,3 @@\n ctx\n+add2\n ctx\n")
    assert sorted(lm.added_new_lines) == [2, 52]
    assert lm.hunk_index_for_new_line(2) == 0
    assert lm.hunk_index_for_new_line(52) == 1
    assert lm.hunk_count == 2


def test_hunk_header_without_counts() -> None:
    """`@@ -1 +1 @@` means a count of 1 on each side."""
    _, lm = parse("@@ -1 +1 @@\n-old\n+new\n")
    assert sorted(lm.added_new_lines) == [1]
    assert sorted(lm.deleted_old_lines) == [1]


def test_zero_context_hunk_for_a_new_file() -> None:
    _, lm = parse("@@ -0,0 +1,3 @@\n+a\n+b\n+c\n", FileChangeType.ADDED)
    assert sorted(lm.added_new_lines) == [1, 2, 3]
    assert not lm.context_new_lines
    assert not lm.deleted_old_lines


def test_whole_file_deletion_has_no_commentable_lines() -> None:
    _, lm = parse("@@ -1,3 +0,0 @@\n-a\n-b\n-c\n", FileChangeType.DELETED)
    assert sorted(lm.deleted_old_lines) == [1, 2, 3]
    assert not lm.commentable_new_lines


def test_large_line_numbers() -> None:
    _, lm = parse("@@ -999997,2 +999998,3 @@\n ctx\n+big\n ctx\n")
    assert sorted(lm.added_new_lines) == [999999]


# -- the classic off-by-ones ---------------------------------------------


def test_no_newline_marker_advances_neither_cursor() -> None:
    """`\\ No newline at end of file` is metadata, not a line.

    Advancing a cursor for it shifts every subsequent line number.
    """
    _, lm = parse(
        "@@ -1,2 +1,2 @@\n ctx\n-old\n\\ No newline at end of file\n"
        "+new\n\\ No newline at end of file\n"
    )
    assert sorted(lm.added_new_lines) == [2]
    assert sorted(lm.deleted_old_lines) == [2]


def test_trailing_newline_does_not_invent_a_phantom_line() -> None:
    """A patch ending in "\\n" splits to a trailing "" that is not a line."""
    _, with_nl = parse("@@ -1,2 +1,2 @@\n ctx\n+added\n")
    _, without_nl = parse("@@ -1,2 +1,2 @@\n ctx\n+added")
    assert with_nl.context_new_lines == without_nl.context_new_lines
    assert sorted(with_nl.context_new_lines) == [1]


def test_crlf_input_is_normalised_but_content_is_preserved() -> None:
    _, lm = parse("@@ -1,1 +1,2 @@\r\n ctx\r\n+added\r\n")
    assert sorted(lm.added_new_lines) == [2]
    assert lm.line_at(2).content == "added"  # no stray \r


@pytest.mark.parametrize(
    "content",
    [
        "\tdef 计算(x):  ",
        "    return '🐛 café'",
        "\t\tif x:   ",
        "  # naïve — résumé",
        "",
    ],
)
def test_content_is_preserved_byte_for_byte(content: str) -> None:
    """Anchor validation in Phase 3 compares exact text.

    Any strip, dedent or normalisation here makes that comparison unreliable.
    """
    _, lm = parse(f"@@ -0,0 +1,1 @@\n+{content}\n", FileChangeType.ADDED)
    assert lm.line_at(1).content == content


# -- absent and malformed patches ----------------------------------------


def test_binary_and_unknown_absences_stay_distinct() -> None:
    """A missing patch must never collapse into "no changes"."""
    binary, _ = parse(None, is_binary=True)
    unknown, _ = parse(None, is_binary=False)
    assert binary.patch_status is PatchStatus.ABSENT_BINARY
    assert unknown.patch_status is PatchStatus.ABSENT_UNKNOWN
    assert not binary.has_usable_patch
    assert not unknown.has_usable_patch


def test_empty_patch_is_empty_not_malformed() -> None:
    """A pure rename touches no lines. That is complete, not broken."""
    fd, lm = parse("", FileChangeType.RENAMED, old_path="old.py", new_path="new.py")
    assert fd.patch_status is PatchStatus.EMPTY
    assert fd.parse_error is None
    assert lm.is_empty


@pytest.mark.parametrize(
    "patch",
    [
        "@@ not a header @@\nstuff\n",
        "no hunk header at all\n",
        "@@ -1,1 +1,1 @@\n?bad marker\n",
    ],
)
def test_malformed_patches_are_contained_not_raised(patch: str) -> None:
    """One malformed file must not abort a whole pull request's analysis."""
    fd, lm = parse(patch)
    assert fd.patch_status is PatchStatus.MALFORMED
    assert fd.parse_error
    assert lm.is_empty


# -- file-level shapes ----------------------------------------------------


def test_rename_retains_both_paths() -> None:
    fd, lm = parse(
        "@@ -1,1 +1,1 @@\n-a\n+b\n",
        FileChangeType.RENAMED,
        old_path="src/old.py",
        new_path="src/new.py",
    )
    assert fd.is_rename
    assert fd.path == "src/new.py"
    assert lm.old_path == "src/old.py"


def test_deleted_file_path_falls_back_to_the_old_path() -> None:
    fd, _ = parse(
        "@@ -1,1 +0,0 @@\n-gone\n",
        FileChangeType.DELETED,
        old_path="src/gone.py",
        new_path=None,
    )
    assert fd.path == "src/gone.py"


def test_unknown_github_status_is_recorded_not_guessed() -> None:
    assert FileChangeType.from_github("teleported") is FileChangeType.UNSUPPORTED
    assert FileChangeType.from_github("renamed") is FileChangeType.RENAMED


def test_deleted_files_have_no_head_version() -> None:
    assert FileChangeType.DELETED.has_head_version is False
    assert FileChangeType.MODIFIED.has_head_version is True


# -- LineMap semantics ----------------------------------------------------


def test_commentable_includes_context_but_not_deletions() -> None:
    """GitHub accepts a comment on any line in the diff, including context.

    Deletions are excluded by policy: `side: LEFT` comments are confusing and
    a reliable source of 422s (architecture.md §9.5).
    """
    _, lm = parse("@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n ctx2\n")
    assert lm.is_commentable(1)  # context
    assert lm.is_commentable(2)  # added
    assert lm.is_changed(2)
    assert not lm.is_changed(1)  # context is commentable but not changed
    assert lm.deleted_line_at(2) is not None
    assert not lm.deleted_line_at(2).is_commentable


def test_line_lookup_returns_none_outside_the_diff() -> None:
    _, lm = parse("@@ -1,1 +1,2 @@\n ctx\n+added\n")
    assert lm.line_at(5000) is None
    assert not lm.is_commentable(5000)


def test_changed_lines_are_sorted_deterministically() -> None:
    _, lm = parse("@@ -1,2 +1,3 @@\n ctx\n+a\n ctx\n@@ -20,1 +21,2 @@\n ctx\n+b\n")
    keys = [entry.sort_key() for entry in lm.changed_lines]
    assert keys == sorted(keys)


def test_every_changed_line_carries_its_file_and_commit() -> None:
    """A changed line without its snapshot cannot anchor anything."""
    _, lm = parse("@@ -1,1 +1,2 @@\n ctx\n+added\n")
    for entry in lm.changed_lines:
        assert entry.file_path == "f.py"
        assert entry.head_sha == HEAD


def test_change_types_are_classified() -> None:
    _, lm = parse("@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n")
    kinds = {entry.change_type for entry in lm.changed_lines}
    assert kinds == {ChangeType.CONTEXT, ChangeType.DELETED, ChangeType.ADDED}
