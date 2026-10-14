"""Source retrieval, line indexing and file classification."""

from app.analysis.source.classifier import (
    ClassificationLimits,
    classify,
    detect_language,
    has_generated_marker,
)
from app.analysis.source.models import (
    FileClassification,
    LineEnding,
    SourceFile,
    SourceStatus,
)
from app.analysis.source.provider import (
    GitHubSourceProvider,
    InMemorySourceProvider,
    SourceProvider,
    SourceRequest,
    SourceRetrievalError,
)

__all__ = [
    "ClassificationLimits",
    "FileClassification",
    "GitHubSourceProvider",
    "InMemorySourceProvider",
    "LineEnding",
    "SourceFile",
    "SourceProvider",
    "SourceRequest",
    "SourceRetrievalError",
    "SourceStatus",
    "classify",
    "detect_language",
    "has_generated_marker",
]
