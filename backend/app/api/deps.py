"""Shared FastAPI dependencies.

Dependencies are declared once here and injected by type annotation, so a
route never constructs its own session, settings object or provider.  That is
what makes routes trivially testable — every collaborator can be overridden
through ``app.dependency_overrides``.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import AIReviewProvider
from app.ai.registry import build_provider
from app.core.config import Settings
from app.db.session import get_session


def provide_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def provide_ai_provider(
    settings: Annotated[Settings, Depends(provide_settings)],
) -> AIReviewProvider:
    """Return the provider selected by ``AI_PROVIDER``.

    Note the return type: routes depend on the *protocol*, never on
    ``GeminiProvider``.  Swapping the vendor changes configuration, not code.
    """
    return build_provider(settings)


SettingsDep = Annotated[Settings, Depends(provide_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AIProviderDep = Annotated[AIReviewProvider, Depends(provide_ai_provider)]
