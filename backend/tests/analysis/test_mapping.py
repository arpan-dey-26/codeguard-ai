"""Changed lines -> AST declarations."""

from __future__ import annotations

from app.analysis.diff import FileChangeType, build_line_map, parse_patch
from app.analysis.languages.python_adapter import PythonAdapter
from app.analysis.mapping.mapper import (
    find_containing_declarations,
    innermost_non_module,
    map_changed_lines,
    map_line,
    touched_declarations,
)
from app.analysis.source.models import SourceFile

HEAD = "d" * 40

SOURCE = """import os


class Invoice:
    TAX = 0.2

    def total(self):
        subtotal = 0
        for line in self.lines:
            subtotal += line
        return subtotal

    def tax(self):
        return self.total() * self.TAX


def helper(x):
    def inner(y):
        return y * 2
    return inner(x)
"""


def decls():  # type: ignore[no-untyped-def]
    source = SourceFile.from_text(path="inv.py", head_sha=HEAD, text=SOURCE)
    return PythonAdapter().parse(source).sorted_declarations()


def test_line_inside_a_method_maps_to_the_method() -> None:
    innermost, chain = map_line(decls(), 9)
    assert innermost.qualified_name == "Invoice.total"
    assert [d.qualified_name for d in chain] == ["<module>", "Invoice", "Invoice.total"]


def test_line_inside_a_class_but_outside_a_method_maps_to_the_class() -> None:
    innermost, _ = map_line(decls(), 5)  # TAX = 0.2
    assert innermost.qualified_name == "Invoice"


def test_line_outside_any_declaration_maps_to_the_module() -> None:
    """An import is module-level. It must not be forced into a fake function."""
    innermost, chain = map_line(decls(), 1)
    assert innermost.qualified_name == "<module>"
    assert len(chain) == 1
    assert innermost_non_module(decls(), 1) is None


def test_nested_function_wins_over_its_parent() -> None:
    innermost, chain = map_line(decls(), 19)  # return y * 2
    assert innermost.qualified_name == "helper.inner"
    assert [d.qualified_name for d in chain] == ["<module>", "helper", "helper.inner"]


def test_declaration_boundaries_are_inclusive() -> None:
    d = {x.qualified_name: x for x in decls()}["Invoice.total"]
    innermost_start, _ = map_line(decls(), d.start_line)
    innermost_end, _ = map_line(decls(), d.end_line)
    assert innermost_start.qualified_name == "Invoice.total"
    assert innermost_end.qualified_name == "Invoice.total"


def test_first_and_last_lines_of_the_file() -> None:
    assert map_line(decls(), 1)[0].qualified_name == "<module>"
    last = SOURCE.count("\n")
    assert map_line(decls(), last)[0] is not None


def test_blank_lines_still_map() -> None:
    innermost, _ = map_line(decls(), 3)  # blank line before the class
    assert innermost.qualified_name == "<module>"


def test_containment_order_is_outermost_to_innermost() -> None:
    chain = find_containing_declarations(decls(), 9)
    spans = [d.line_span for d in chain]
    assert spans == sorted(spans, reverse=True), "chain must narrow"


def test_mapping_a_real_diff() -> None:
    # The post-image of this patch must BE `SOURCE`, or the mapping is being
    # asked about lines that do not exist in the file we parsed.
    patch = (
        "@@ -7,5 +7,5 @@ class Invoice:\n"
        "     def total(self):\n"
        "         subtotal = 0\n"
        "         for line in self.lines:\n"
        "-            subtotal += line.amount\n"
        "+            subtotal += line\n"
        "         return subtotal\n"
    )
    fd = parse_patch(
        patch, old_path="inv.py", new_path="inv.py", change_type=FileChangeType.MODIFIED
    )
    line_map = build_line_map(fd, head_sha=HEAD)
    mapping = map_changed_lines(line_map=line_map, declarations=decls())

    assert mapping.file_path == "inv.py"
    assert mapping.head_sha == HEAD
    assert all(m.qualified_name == "Invoice.total" for m in mapping.mappings)
    assert not mapping.unmapped_lines
    assert mapping.mappings == tuple(sorted(mapping.mappings, key=lambda m: m.line))


def test_a_line_past_a_declaration_maps_to_its_parent_not_to_it() -> None:
    """Guards the boundary: line 12 is inside the class but outside `total`."""
    patch = (
        "@@ -7,6 +7,7 @@ class Invoice:\n"
        "     def total(self):\n"
        "         subtotal = 0\n"
        "         for line in self.lines:\n"
        "             subtotal += line\n"
        "         return subtotal\n"
        "+\n"
        "     def tax(self):\n"
    )
    fd = parse_patch(
        patch, old_path="inv.py", new_path="inv.py", change_type=FileChangeType.MODIFIED
    )
    mapping = map_changed_lines(line_map=build_line_map(fd, head_sha=HEAD), declarations=decls())
    twelve = mapping.for_line(12)
    assert twelve is not None and twelve.qualified_name == "Invoice"


def test_deleted_lines_are_not_mapped_against_head_declarations() -> None:
    """A deleted line's number refers to the base file.

    Looking it up against head-parsed declarations returns a confidently wrong
    answer, so deletions are excluded by default.
    """
    patch = "@@ -7,3 +7,2 @@\n     def total(self):\n-        subtotal = 0\n         return 1\n"
    fd = parse_patch(
        patch, old_path="inv.py", new_path="inv.py", change_type=FileChangeType.MODIFIED
    )
    line_map = build_line_map(fd, head_sha=HEAD)
    mapping = map_changed_lines(line_map=line_map, declarations=decls())
    assert all(m.side == "RIGHT" for m in mapping.mappings)
    assert line_map.deleted_old_lines  # still recorded on the LineMap


def test_touched_declarations_include_the_parent_chain() -> None:
    patch = "@@ -9,1 +9,2 @@\n             subtotal += line\n+            pass\n"
    fd = parse_patch(
        patch, old_path="inv.py", new_path="inv.py", change_type=FileChangeType.MODIFIED
    )
    mapping = map_changed_lines(line_map=build_line_map(fd, head_sha=HEAD), declarations=decls())
    touched = {d.qualified_name for d in touched_declarations(decls(), mapping)}
    assert {"<module>", "Invoice", "Invoice.total"} <= touched


def test_mapping_is_deterministic_across_runs() -> None:
    patch = "@@ -9,1 +9,2 @@\n             subtotal += line\n+            pass\n"
    fd = parse_patch(
        patch, old_path="inv.py", new_path="inv.py", change_type=FileChangeType.MODIFIED
    )
    line_map = build_line_map(fd, head_sha=HEAD)
    first = map_changed_lines(line_map=line_map, declarations=decls())
    second = map_changed_lines(line_map=line_map, declarations=decls())
    assert first == second


def test_no_declarations_means_everything_is_unmapped_not_crashed() -> None:
    patch = "@@ -1,1 +1,2 @@\n ctx\n+added\n"
    fd = parse_patch(patch, old_path="x.py", new_path="x.py", change_type=FileChangeType.MODIFIED)
    mapping = map_changed_lines(line_map=build_line_map(fd, head_sha=HEAD), declarations=())
    assert mapping.unmapped_lines
    assert not mapping.mappings
