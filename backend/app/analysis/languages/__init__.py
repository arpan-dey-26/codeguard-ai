"""Language detection and per-language declaration extraction."""

from app.analysis.languages.base import (
    AdapterRegistry,
    Declaration,
    DeclarationKind,
    LanguageAdapter,
    ParseResult,
    ParseStatus,
    make_declaration_id,
)
from app.analysis.languages.python_adapter import PythonAdapter
from app.analysis.languages.treesitter_adapter import (
    TreeSitterAdapter,
    java_adapter,
    javascript_adapter,
    typescript_adapter,
)


def build_registry() -> AdapterRegistry:
    """The default registry.

    Adding a language is one line here plus one adapter — nothing in the
    pipeline, the mapper or the diff layer changes.
    """
    registry = AdapterRegistry()
    registry.register(PythonAdapter())
    registry.register(javascript_adapter())
    registry.register(typescript_adapter())
    registry.register(java_adapter())
    return registry


__all__ = [
    "AdapterRegistry",
    "Declaration",
    "DeclarationKind",
    "LanguageAdapter",
    "ParseResult",
    "ParseStatus",
    "PythonAdapter",
    "TreeSitterAdapter",
    "build_registry",
    "java_adapter",
    "javascript_adapter",
    "make_declaration_id",
    "typescript_adapter",
]
