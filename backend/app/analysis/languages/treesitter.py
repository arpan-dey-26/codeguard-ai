"""Tree-sitter backend.

Loading is lazy and failure is graceful. If ``tree_sitter`` or a grammar is not
installed, ``get_parser`` returns ``None`` and the adapter reports
``PARSER_UNAVAILABLE`` for that language — a deployment fact, recorded
honestly, not a source-code failure and never a crash.

That distinction earns its keep: a missing grammar and a syntactically broken
file are different problems with different fixes, and an artifact that
conflates them sends someone to debug the wrong thing.

**Nothing here executes repository content.** Tree-sitter builds a syntax tree
from bytes. It has no evaluator, runs no code from the file, and produces a
tree even for input that is not valid in the language — which is exactly why
it is the right parser for pull-request code.
"""

from __future__ import annotations

import importlib
import threading
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Grammar module names, in preference order, per language. The per-language
# packages are tried first because they pin one grammar version; the bundles
# are a convenience fallback.
_GRAMMAR_MODULES: dict[str, tuple[str, ...]] = {
    "python": ("tree_sitter_python",),
    "javascript": ("tree_sitter_javascript",),
    "typescript": ("tree_sitter_typescript",),
    "java": ("tree_sitter_java",),
}

_lock = threading.Lock()
_parsers: dict[str, Any] = {}
_unavailable: dict[str, str] = {}


def _load_core() -> Any | None:
    try:
        tree_sitter = importlib.import_module("tree_sitter")
    except ImportError:
        return None
    return tree_sitter


def _build_language(language: str, tree_sitter: Any) -> Any | None:
    """Return a ``Language`` for ``language``, or ``None`` if unobtainable."""
    for module_name in _GRAMMAR_MODULES.get(language, ()):
        # The name comes from the module-level constant above and never from
        # input, configuration or repository content. This imports a *parser
        # grammar package* — it never imports anything from a repository under
        # analysis, which the security suite asserts.
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        try:
            # py-tree-sitter 0.22+ grammars expose `language()` returning a
            # PyCapsule that `Language(...)` wraps. TypeScript's package
            # exposes two dialects.
            if language == "typescript" and hasattr(module, "language_typescript"):
                capsule = module.language_typescript()
            else:
                capsule = module.language()
            return tree_sitter.Language(capsule)
        except Exception as exc:
            logger.warning(
                "treesitter.grammar.load_failed",
                language=language,
                module=module_name,
                error_type=type(exc).__name__,
            )
            continue
    return None


def get_parser(language: str) -> Any | None:
    """Return a cached Tree-sitter parser, or ``None`` when unavailable.

    Thread-safe and memoised in both directions: a language that failed to load
    is not retried on every file, because an import that failed once will fail
    identically for the rest of the process's life.
    """
    if language in _parsers:
        return _parsers[language]
    if language in _unavailable:
        return None

    with _lock:
        if language in _parsers:
            return _parsers[language]
        if language in _unavailable:
            return None

        tree_sitter = _load_core()
        if tree_sitter is None:
            _unavailable[language] = "tree_sitter is not installed"
            return None

        ts_language = _build_language(language, tree_sitter)
        if ts_language is None:
            _unavailable[language] = f"no grammar available for {language}"
            return None

        try:
            parser = tree_sitter.Parser(ts_language)
        except TypeError:
            # Older binding: Parser() then .set_language().
            parser = tree_sitter.Parser()
            parser.set_language(ts_language)
        except Exception as exc:
            _unavailable[language] = f"parser construction failed: {type(exc).__name__}"
            return None

        _parsers[language] = parser
        logger.info("treesitter.parser.ready", language=language)
        return parser


def is_available(language: str) -> bool:
    return get_parser(language) is not None


def unavailable_reason(language: str) -> str | None:
    get_parser(language)
    return _unavailable.get(language)


def count_error_nodes(node: Any, *, limit: int = 1000) -> int:
    """Count ERROR and MISSING nodes, breadth-first and bounded.

    Bounded because a pathological file can produce an enormous error tree, and
    an unbounded walk would turn one bad file into a stalled worker.
    """
    total = 0
    queue = [node]
    visited = 0
    while queue and total < limit and visited < 100_000:
        current = queue.pop()
        visited += 1
        if getattr(current, "is_error", False) or getattr(current, "is_missing", False):
            total += 1
        if getattr(current, "has_error", False) or total == 0:
            queue.extend(getattr(current, "children", ()))
    return total


def node_text(node: Any, source_bytes: bytes) -> str:
    """Exact source text for a node, decoded permissively.

    Used only for names and signatures — never for anchor text, which always
    comes from ``SourceFile`` so that one decoding path governs anchoring.
    """
    try:
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", "replace")
    except Exception:
        return ""


__all__ = [
    "count_error_nodes",
    "get_parser",
    "is_available",
    "node_text",
    "unavailable_reason",
]
