"""Exercise dashboard bound parameters against PostgreSQL, including casts."""

from datetime import UTC, datetime

import pytest
from app.api.filters import TimeRange
from app.api.scope import ViewerScope
from app.db.repositories import dashboard
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL, requires_database

pytestmark = [pytest.mark.integration, requires_database]


async def test_dashboard_trends_and_uuid_queries_bind_on_postgresql() -> None:
    scope = ViewerScope.unrestricted()
    missing_id = "00000000-0000-0000-0000-000000000000"
    queries = [
        dashboard.review_trends(
            scope=scope,
            time_range=TimeRange(
                start=datetime(2020, 1, 1, tzinfo=UTC),
                end=datetime(2020, 1, 3, tzinfo=UTC),
            ),
        ),
        dashboard.review_run_detail(scope=scope, review_run_id=missing_id),
        dashboard.review_run_changed_files(review_run_id=missing_id),
        dashboard.finding_detail(scope=scope, finding_id=missing_id),
        dashboard.finding_events(finding_id=missing_id),
    ]
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            results = []
            for query in queries:
                results.append((await conn.execute(text(query.sql), query.params)).all())
        assert len(results[0]) == 2
        assert all(not rows for rows in results[1:])
    finally:
        await engine.dispose()
