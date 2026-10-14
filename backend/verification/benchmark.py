"""Local timings for the CPU-bound stages. Phase 6 §25.

**These are not SLA numbers and must not be presented as any.** They were taken
on one shared cloud container, on synthetic inputs, with no GitHub and no AI
provider in the loop — the two stages that dominate real wall-clock time and
that could not be measured at all because no credentials exist. What they do
establish is narrower and still worth having: the stages that *are* under this
project's control scale the way the design claims, and none of them hides a
quadratic.

Each figure is the median of N runs after a warm-up, with the spread reported,
because a single timing on a shared machine is noise.
"""

from __future__ import annotations

import os as _os
import pathlib as _pathlib

_HERE = _pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = _pathlib.Path(_os.environ.get("CODEGUARD_BACKEND", _HERE.parent))
PG_SOCKET_DIR = _os.environ.get("CODEGUARD_PGDIR", "/var/tmp/pg3")
PG_PORT = _os.environ.get("CODEGUARD_PGPORT", "55433")


import gc
import statistics
import sys
import time

sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.contracts import FindingCategory, FindingSeverity, RawFinding  # noqa: E402
from app.analysis.diff.models import FileChangeType  # noqa: E402
from app.analysis.diff.parser import build_line_map, parse_patch  # noqa: E402
from app.analysis.languages.python_adapter import PythonAdapter  # noqa: E402
from app.review.context import ContextBudget, build_review_context  # noqa: E402
from app.review.publisher import render_comment_body  # noqa: E402
from app.review.validator import FindingValidator  # noqa: E402
from tests.review.fixtures import (  # noqa: E402
    FILE_PATH,
    HEAD_SHA,
    PATCH,
    SOURCE,
    build_analysis,
    build_file_analysis,
)

RUNS = 15


def measure(label: str, fn, *, runs: int = RUNS, scale: str = "") -> dict[str, float]:
    fn()  # warm-up: first call pays for imports, caches and branch prediction
    gc.collect()
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    median = statistics.median(samples)
    print(
        f"  {label:<52} {median:8.2f} ms   "
        f"(min {min(samples):7.2f}, max {max(samples):7.2f})   {scale}"
    )
    return {"label": label, "median_ms": median, "min_ms": min(samples), "max_ms": max(samples)}


def _big_patch(hunks: int) -> str:
    """A patch with many hunks, to exercise the parser rather than one hunk."""
    parts = []
    for index in range(hunks):
        start = 9 + index * 40
        parts.append(
            f"@@ -{start},6 +{start},12 @@ class Big:\n"
            "     def keep(self):\n"
            "         return 1\n"
            "\n"
            f"+    def added_{index}(self, key):\n"
            f"+        value = compute_{index}(key)\n"
            "+        return value\n"
            "+\n"
            "+\n"
            "+\n"
            "     def tail(self):\n"
            "         return 2\n"
        )
    return "".join(parts)


def _finding(index: int) -> RawFinding:
    return RawFinding(
        finding_id=f"F{index:03d}",
        category=FindingCategory.RESOURCE,
        severity=FindingSeverity.HIGH,
        title=f"Finding number {index}",
        description="open() is called without a context manager.",
        file_path=FILE_PATH,
        line_start=14,
        anchor_snippet="handle = open(path)",
        confidence=0.9,
        impact="File descriptors leak until the process exits.",
        rationale="No close() and no with-statement.",
    )


def main() -> None:
    print(__doc__.strip())
    print("\nEnvironment: python", sys.version.split()[0], "— one shared cloud container\n")
    results = []

    print("Diff parsing (app/analysis/diff/parser.py)")
    for hunks in (1, 10, 100):
        patch = _big_patch(hunks)
        results.append(
            measure(
                f"parse_patch + build_line_map, {hunks} hunks",
                lambda p=patch: build_line_map(
                    parse_patch(p, old_path="a.py", new_path="a.py", change_type=FileChangeType.MODIFIED),
                    head_sha=HEAD_SHA,
                ),
                scale=f"{len(patch):>7} patch bytes",
            )
        )

    print("\nPython AST analysis (app/analysis/languages/python_adapter.py)")
    adapter = PythonAdapter()
    for multiple in (1, 20, 200):
        source = SOURCE * multiple
        results.append(
            measure(
                f"adapter.parse, {multiple}x fixture",
                lambda s=source: adapter.parse(
                    __import__("app.analysis.source.models", fromlist=["SourceFile"]).SourceFile.from_text(
                        path=FILE_PATH, head_sha=HEAD_SHA, text=s
                    )
                ),
                scale=f"{len(source):>7} source bytes",
            )
        )

    print("\nContext building (app/review/context.py)")
    for count in (1, 40, 500, 3000):
        analysis = build_analysis(
            files=[build_file_analysis(path=f"app/mod_{i:04d}.py") for i in range(count)]
        )
        results.append(
            measure(
                f"build_review_context, {count} changed files",
                lambda a=analysis: build_review_context(a, budget=ContextBudget()),
                runs=5 if count >= 500 else RUNS,
                scale=f"{count:>7} files in the PR",
            )
        )

    print("\nValidation (app/review/validator.py)")
    analysis = build_analysis()
    context = build_review_context(analysis, budget=ContextBudget())
    analyses = {f.file_path: f for f in analysis.files}
    validator = FindingValidator()
    for count in (1, 25, 200):
        claims = [_finding(i) for i in range(count)]
        results.append(
            measure(
                f"validate, {count} model claims",
                lambda c=claims: validator.validate(
                    c, context=context, analyses=analyses, head_sha=HEAD_SHA
                ),
                scale=f"{count:>7} claims",
            )
        )

    print("\nComment rendering (app/review/publisher.py)")
    report = validator.validate(
        [_finding(0)], context=context, analyses=analyses, head_sha=HEAD_SHA
    )
    validated = report.validated[0]
    results.append(
        measure("render_comment_body, one finding", lambda: render_comment_body(validated))
    )

    print(
        "\nNot measured, and not estimated: the GitHub round trips and the AI\n"
        "provider call. No credentials for either exist in any environment this\n"
        "project has been built or verified in, so their latency is unknown.\n"
        "They dominate a real review's wall-clock time, which is why no\n"
        "end-to-end number appears anywhere in this report."
    )


if __name__ == "__main__":
    main()
