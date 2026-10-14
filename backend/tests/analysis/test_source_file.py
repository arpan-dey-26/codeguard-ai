"""Exact source-line indexing.

The anchor layer in Phase 3 compares model-quoted text against these lines, so
any normalisation here becomes a silent validation failure there.
"""

from __future__ import annotations

from app.analysis.source.models import LineEnding, SourceFile
from app.analysis.source.provider import SourceRetrievalError, decode_source

HEAD = "b" * 40


def sf(text: str) -> SourceFile:
    return SourceFile.from_text(path="a.py", head_sha=HEAD, text=text)


def test_lines_are_one_based_and_exact() -> None:
    f = sf("first\nsecond\nthird\n")
    assert f.line_count == 3
    assert f.line(1) == "first"
    assert f.line(3) == "third"
    assert f.line(0) is None
    assert f.line(4) is None


def test_out_of_range_returns_none_not_empty_string() -> None:
    """ "Line 5000 of a 3-line file" must be distinguishable from "a blank line"."""
    f = sf("a\n\nc\n")
    assert f.line(2) == ""  # a real, blank line
    assert f.line(99) is None  # not a line at all
    assert f.contains_line(2)
    assert not f.contains_line(99)


def test_final_newline_does_not_create_a_phantom_line() -> None:
    assert sf("a\nb\n").line_count == 2
    assert sf("a\nb").line_count == 2
    assert sf("a\nb\n").had_final_newline
    assert not sf("a\nb").had_final_newline


def test_crlf_lines_do_not_retain_the_carriage_return() -> None:
    """The diff layer strips terminators too; both sides must agree."""
    f = sf("first\r\nsecond\r\n")
    assert f.line(1) == "first"
    assert f.line_ending is LineEnding.CRLF


def test_mixed_line_endings_are_reported() -> None:
    assert sf("a\r\nb\nc\n").line_ending is LineEnding.MIXED
    assert sf("a\nb\n").line_ending is LineEnding.LF
    assert sf("single line").line_ending is LineEnding.NONE


def test_tabs_unicode_and_trailing_whitespace_are_preserved() -> None:
    text = "\tdef 计算(x):  \n    return '🐛 café'\t\n"
    f = sf(text)
    assert f.line(1) == "\tdef 计算(x):  "
    assert f.line(2) == "    return '🐛 café'\t"


def test_exotic_separators_do_not_split_lines() -> None:
    """str.splitlines() breaks on \\x0b, \\x0c and \\u2028 — inside real source.

    Splitting on them would invent lines and shift every number after.
    """
    f = sf("a = '\x0b\x0c\u2028'\nb = 2\n")
    assert f.line_count == 2
    assert f.line(2) == "b = 2"


def test_very_long_line_is_intact() -> None:
    long_line = "x" * 200_000
    f = sf(f"{long_line}\nshort\n")
    assert f.line(1) == long_line
    assert f.line(2) == "short"


def test_empty_file_has_no_lines() -> None:
    assert sf("").line_count == 0
    assert sf("").line(1) is None


def test_slices_are_inclusive_and_clamped() -> None:
    f = sf("1\n2\n3\n4\n5\n")
    assert f.lines_in(2, 4) == ("2", "3", "4")
    assert f.lines_in(4, 99) == ("4", "5")
    assert f.lines_in(99, 100) == ()
    assert f.lines_in(3, 2) == ()


def test_decode_rejects_binary_content() -> None:
    """A NUL byte in the first 8 KiB is git's own binary heuristic."""
    try:
        decode_source(b"PK\x03\x04\x00\x00binary", path="a.zip", head_sha=HEAD)
    except SourceRetrievalError as exc:
        assert "binary" in str(exc).lower()
    else:
        raise AssertionError("binary content was accepted")


def test_decode_rejects_invalid_utf8_rather_than_replacing() -> None:
    """errors='replace' would corrupt the exact text anchoring depends on."""
    try:
        decode_source(b"valid \xff\xfe invalid", path="a.py", head_sha=HEAD)
    except SourceRetrievalError as exc:
        assert "utf-8" in str(exc).lower()
    else:
        raise AssertionError("invalid UTF-8 was accepted")


def test_decode_accepts_valid_utf8() -> None:
    f = decode_source("héllo\nwörld\n".encode(), path="a.py", head_sha=HEAD)
    assert f.line(1) == "héllo"
    assert f.byte_size == len("héllo\nwörld\n".encode())
