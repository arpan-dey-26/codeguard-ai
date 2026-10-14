"""Shared test fixtures.

Design rules for the whole suite, unchanged from Phase 0 and extended here:

*   **No test requires a network call, an API key, or real GitHub credentials.**
    GitHub HTTP is served by ``httpx.MockTransport``; the GitHub App private
    key used in tests is a throwaway RSA key generated at run time.
*   Database-backed tests are marked ``integration`` and skip themselves when
    no PostgreSQL is reachable, so ``pytest`` is green on a laptop with nothing
    running while still exercising the real database in CI and in Docker.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager

import pytest
from app.core.config import AppEnv, Settings, get_settings

# FastAPI is imported lazily, and that is a deliberate Phase 6 change.
#
# Until Phase 6 this module imported ``fastapi`` at the top. A missing FastAPI
# therefore raised during **conftest collection**, which aborts the entire
# session — so a machine without the web framework could not run the validator
# tests, the diff-parser tests or anything else that has no web layer at all.
# A test suite that is all-or-nothing on one optional dependency is a test
# suite that mostly does not get run.
#
# The fixtures that genuinely need FastAPI now skip themselves; everything else
# runs. ``pytest.importorskip`` reports the reason in the skip message rather
# than presenting it as a pass.
try:  # pragma: no cover - import-time capability probe
    from fastapi import FastAPI as _FastAPI
    from fastapi.testclient import TestClient as _TestClient

    FASTAPI_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    _FastAPI = object  # type: ignore[assignment,misc]
    _TestClient = object  # type: ignore[assignment,misc]
    FASTAPI_AVAILABLE = False

requires_fastapi = pytest.mark.skipif(
    not FASTAPI_AVAILABLE,
    reason="FastAPI is not installed in this environment",
)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://codeguard:codeguard@localhost:5432/codeguard",
)

# A fixture value, not a credential: it never authenticates anything real.
WEBHOOK_SECRET = "test-webhook-secret-do-not-use"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the cached settings singleton from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def webhook_secret() -> str:
    return WEBHOOK_SECRET


@pytest.fixture
def settings() -> Settings:
    """Settings for a test run: no credentials of any kind."""
    return Settings(
        app_env=AppEnv.TEST,
        debug=False,
        database_url=TEST_DATABASE_URL,
        frontend_url="http://localhost:5173",
        log_level="WARNING",
    )


@pytest.fixture
def webhook_settings() -> Settings:
    """Settings with a webhook secret, for exercising the ingress endpoint."""
    return Settings(
        app_env=AppEnv.TEST,
        debug=False,
        database_url=TEST_DATABASE_URL,
        frontend_url="http://localhost:5173",
        log_level="WARNING",
        github_webhook_secret=WEBHOOK_SECRET,
        github_app_id="123456",
        github_webhook_max_body_bytes=1024 * 1024,
    )


def _create_app(settings: Settings):  # type: ignore[no-untyped-def]
    """Import the application factory only when it is actually needed."""
    from app.main import create_app

    return create_app(settings)


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    if not FASTAPI_AVAILABLE:
        pytest.skip("FastAPI is not installed in this environment")
    return _create_app(settings)


@pytest.fixture
def client(app) -> Iterator[object]:  # type: ignore[no-untyped-def]
    with _TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def webhook_client(webhook_settings: Settings) -> Iterator[object]:
    """Client whose app has a webhook secret and a stubbed database session.

    The endpoint's own behaviour — signature enforcement, header validation,
    payload handling, status codes — is independent of persistence, so these
    tests substitute the session dependency. Idempotency and the transactional
    enqueue are exercised against a real PostgreSQL in ``tests/integration``.
    """
    if not FASTAPI_AVAILABLE:
        pytest.skip("FastAPI is not installed in this environment")
    from app.db.session import get_session

    app = _create_app(webhook_settings)
    app.dependency_overrides[get_session] = _fake_session_dependency
    with _TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def unconfigured_webhook_client(settings: Settings) -> Iterator[object]:
    """An app with **no** webhook secret configured."""
    if not FASTAPI_AVAILABLE:
        pytest.skip("FastAPI is not installed in this environment")
    from app.db.session import get_session

    app = _create_app(settings)
    app.dependency_overrides[get_session] = _fake_session_dependency
    with _TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class _FakeResult:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    def first(self) -> tuple[object, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[object, ...]]:
        return []

    def mappings(self) -> _FakeResult:
        return self

    def scalar_one(self) -> object:
        return 1


class _FakeSession:
    """Minimal stand-in for ``AsyncSession`` in endpoint-level tests.

    Deliberately tiny: it exists so the HTTP layer can be tested in isolation,
    not to simulate a database. Anything that depends on real SQL semantics
    belongs in an integration test against PostgreSQL.
    """

    def __init__(self) -> None:
        self.committed = False
        self._next_id = 1

    @asynccontextmanager
    async def begin_nested(self):  # type: ignore[no-untyped-def]
        """Endpoint-only fixture; real savepoints are tested with PostgreSQL."""
        yield self

    async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
        row = (self._next_id,)
        self._next_id += 1
        return _FakeResult(row)

    def add(self, obj: object) -> None:
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            obj.id = self._next_id  # type: ignore[attr-defined]
            self._next_id += 1

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


async def _fake_session_dependency():  # type: ignore[no-untyped-def]
    yield _FakeSession()


def database_available(url: str = TEST_DATABASE_URL) -> bool:
    """True when a PostgreSQL instance accepts a connection at ``url``."""

    async def _probe() -> bool:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


requires_database = pytest.mark.skipif(
    not database_available(),
    reason="PostgreSQL is not reachable at TEST_DATABASE_URL",
)


# ---------------------------------------------------------------------------
# Running async tests without pytest-asyncio
# ---------------------------------------------------------------------------
# `pytest-asyncio` is a declared dev dependency and is the right tool. When it
# is absent, pytest does not await a coroutine test — it calls the function,
# gets a coroutine object back, warns, and records a PASS. **A suite that
# silently passes every async test it never ran is worse than one that fails
# to collect**, so this hook runs them instead.
#
# It is deliberately minimal and defers to the real plugin whenever the plugin
# is installed, so it changes nothing in CI. What it is not: a reimplementation
# of pytest-asyncio's fixture handling — an async *fixture* still needs the
# plugin, and a test that requires one will error rather than quietly pass.
try:  # pragma: no cover - capability probe
    import pytest_asyncio  # noqa: F401

    PYTEST_ASYNCIO_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    PYTEST_ASYNCIO_AVAILABLE = False


if not PYTEST_ASYNCIO_AVAILABLE:  # pragma: no cover - environment dependent

    def pytest_configure(config: object) -> None:
        config.addinivalue_line(  # type: ignore[attr-defined]
            "markers", "asyncio: run this coroutine test on a fresh event loop"
        )

    @pytest.hookimpl(tryfirst=True)
    def pytest_pyfunc_call(pyfuncitem: object):  # type: ignore[no-untyped-def]
        import inspect

        func = pyfuncitem.obj  # type: ignore[attr-defined]
        if not inspect.iscoroutinefunction(func):
            return None
        kwargs = {
            name: pyfuncitem.funcargs[name]  # type: ignore[attr-defined]
            for name in pyfuncitem._fixtureinfo.argnames  # type: ignore[attr-defined]
        }
        # A new loop per test: a shared loop lets one test's pending task fail
        # a later, unrelated one, which is the hardest kind of flake to trace.
        asyncio.run(func(**kwargs))
        return True
