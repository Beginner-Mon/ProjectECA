"""Contract for the rate counter, memory and Postgres.

The `pg` param is marked `integration`: it runs in the GitHub Actions job
`Loop PG contract` against a throwaway Postgres, as `eca_user`, through the
`test_pg_client` fixture (never `get_pg_client()`, which is the app database).
Locally it skips.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from langgraph_agents.loop.rate import MemoryRateCounter, PgRateCounter


@pytest.fixture(params=[
    pytest.param("memory", id="memory"),
    pytest.param("pg", id="pg", marks=pytest.mark.integration),
])
def counter(request):
    """(counter, bucket). The bucket is unique per test, and its rows are
    removed afterwards on PG."""
    bucket = f"test-{uuid.uuid4()}"
    if request.param == "memory":
        return MemoryRateCounter(), bucket
    pg = request.getfixturevalue("test_pg_client")

    async def _cleanup():
        async with pg.get_client().transaction() as conn:
            await conn.execute("DELETE FROM rate_counters WHERE bucket = $1", bucket)

    request.addfinalizer(lambda: _swallow(_cleanup))
    return PgRateCounter(get_pg_client=pg.get_client), bucket


def _swallow(coro_fn) -> None:
    try:
        asyncio.run(coro_fn())
    except Exception:  # noqa: BLE001 - cleanup must not mask the test result
        pass


def test_hits_are_allowed_up_to_the_limit_then_refused(counter):
    c, bucket = counter
    user = str(uuid.uuid4())

    async def _go():
        return [await c.hit(user, bucket, limit=3) for _ in range(5)]

    # A minute boundary between hits would reset the PG window (about a 1 in
    # 600 chance for five quick calls); one retry removes that flake.
    for _ in range(2):
        got = asyncio.run(_go())
        if got == [True, True, True, False, False]:
            break
        user = str(uuid.uuid4())
    assert got == [True, True, True, False, False]


def test_users_have_separate_counts(counter):
    c, bucket = counter
    a, b = str(uuid.uuid4()), str(uuid.uuid4())

    async def _go():
        first = [await c.hit(a, bucket, limit=1) for _ in range(2)]
        other = await c.hit(b, bucket, limit=1)
        return first, other

    first, other = asyncio.run(_go())
    assert first == [True, False]
    assert other is True
