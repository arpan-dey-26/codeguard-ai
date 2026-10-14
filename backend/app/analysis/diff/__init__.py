"""Unified-diff parsing and the LineMap.

Pure: no GitHub, no database, no clock. Testable standalone, and deliberately
so — every AI finding in Phase 3 is validated against what this layer produces.
"""

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
from app.analysis.diff.parser import build_line_map, parse_patch

__all__ = [
    "ChangeType",
    "ChangedLine",
    "DiffLine",
    "FileChangeType",
    "FileDiff",
    "Hunk",
    "LineMap",
    "PatchStatus",
    "Side",
    "build_line_map",
    "parse_patch",
]
