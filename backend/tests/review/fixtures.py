"""Shared Phase 3 fixtures: a real diff, real source, a real analysis.

Built through the *actual* Phase 2 pipeline components — ``parse_patch``,
``build_line_map``, the Python adapter, ``map_changed_lines`` — rather than by
hand-constructing a ``FileAnalysis``. A hand-built fixture proves the validator
agrees with the fixture; this proves it agrees with the diff parser.
"""

from __future__ import annotations

from app.analysis.artifact import FileAnalysis, FileAnalysisOutcome, PullRequestAnalysis
from app.analysis.diff.models import FileChangeType, PatchStatus
from app.analysis.diff.parser import build_line_map, parse_patch
from app.analysis.languages.python_adapter import PythonAdapter
from app.analysis.mapping.mapper import map_changed_lines
from app.analysis.source.models import SourceFile, SourceStatus

HEAD_SHA = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
OTHER_SHA = "0000000000000000000000000000000000000000"

SOURCE = '''\
"""Session handling."""

import os


class SessionStore:
    """Keeps sessions on disk."""

    def __init__(self, root):
        self.root = root

    def load(self, key):
        path = os.path.join(self.root, key)
        handle = open(path)
        data = handle.read()
        return data

    def save(self, key, data):
        path = os.path.join(self.root, key)
        with open(path, "w") as handle:
            handle.write(data)
        return True
'''

# Adds `load` (lines 12-16) to a file that previously ended after __init__.
PATCH = """\
@@ -9,6 +9,12 @@ class SessionStore:
     def __init__(self, root):
         self.root = root

+    def load(self, key):
+        path = os.path.join(self.root, key)
+        handle = open(path)
+        data = handle.read()
+        return data
+
     def save(self, key, data):
         path = os.path.join(self.root, key)
         with open(path, "w") as handle:
"""

FILE_PATH = "app/session.py"


def build_file_analysis(
    *,
    path: str = FILE_PATH,
    source_text: str = SOURCE,
    patch: str = PATCH,
    head_sha: str = HEAD_SHA,
    truncated: bool = False,
    outcome: FileAnalysisOutcome = FileAnalysisOutcome.ANALYSED,
) -> FileAnalysis:
    source = SourceFile.from_text(path=path, head_sha=head_sha, text=source_text)
    file_diff = parse_patch(
        patch, old_path=path, new_path=path, change_type=FileChangeType.MODIFIED
    )
    line_map = build_line_map(file_diff, head_sha=head_sha)

    adapter = PythonAdapter()
    parse_result = adapter.parse(source)
    declarations = parse_result.sorted_declarations()
    mapping = map_changed_lines(line_map=line_map, declarations=declarations)

    return FileAnalysis(
        file_path=path,
        old_path=None,
        head_sha=head_sha,
        change_type=FileChangeType.MODIFIED,
        outcome=outcome,
        source_status=SourceStatus.AVAILABLE,
        patch_status=PatchStatus.PRESENT,
        language="python",
        parse_status=parse_result.status,
        parser_backend=parse_result.parser_backend,
        line_count=source.line_count,
        changed_line_count=len(line_map.changed_lines),
        added_line_count=len(line_map.added_new_lines),
        deleted_line_count=len(line_map.deleted_old_lines),
        declarations=declarations,
        mapping=mapping,
        truncated=truncated,
        # Kept even when truncated: production does the same, and
        # ``FileAnalysis.is_authoritative`` — not the absence of source —
        # is what stops a truncated file being trusted for anchoring.
        source=source,
        line_map=line_map,
    )


def build_analysis(
    files: list[FileAnalysis] | None = None, *, head_sha: str = HEAD_SHA
) -> PullRequestAnalysis:
    files = files if files is not None else [build_file_analysis(head_sha=head_sha)]
    return PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=head_sha,
        files=tuple(files),
        total_files=len(files),
        analysed_files=len(files),
    )


WIDE_SOURCE = '''\
class Wide:
    """A class with several methods."""

    def alpha(self):
        return 1

    def beta(self, n):
        total = 0
        for i in range(n):
            total += i
        return total

    # ------------------------------------------------------------------
    # The section below is untouched by the pull request.
    # ------------------------------------------------------------------

    def gamma(self):
        value = self.alpha()
        return value * 2
'''

# Rewrites only `beta`. Nothing in the hunk reaches `gamma`.
WIDE_PATCH = """\
@@ -7,5 +7,6 @@ class Wide:
     def beta(self, n):
         total = 0
         for i in range(n):
-            total = total + i
+            total += i
         return total
"""


def build_wide_analysis(*, head_sha: str = HEAD_SHA) -> PullRequestAnalysis:
    """A class whose third method is well outside the changed hunk."""
    return build_analysis(
        [
            build_file_analysis(
                path="app/wide.py",
                source_text=WIDE_SOURCE,
                patch=WIDE_PATCH,
                head_sha=head_sha,
            )
        ],
        head_sha=head_sha,
    )
