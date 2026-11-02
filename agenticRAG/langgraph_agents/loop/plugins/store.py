"""Which plugins a user has switched on or off (`user_plugins`, migration 020).

One row per (user, plugin) that the user has touched. A missing row means "the
manifest's `enabled_by_default`" (D3), so the table stays small and a new
catalog entry needs no backfill.

Two implementations behind one Protocol, with one contract suite
(`test_plugins.py`):

* `MemoryPluginStore`: in-process, for tests and local dev.
* `PgPluginStore`: asyncpg, through `PostgresClient.user_scope(user_id)` so the
  RLS owner policy of 020 applies. A turn reads the whole map ONCE (one
  query); never per call.

`get_plugin_store()` returns the Postgres store unless
`VVA_LOOP_LEASE_STORE=memory` (the switch the lease and rate stores share).
"""

from __future__ import annotations

import os
from typing import Optional, Protocol


class PluginStore(Protocol):
    async def get_all(self, user_id: str) -> dict[str, bool]: ...

    async def set_enabled(self, user_id: str, plugin_id: str, enabled: bool) -> None: ...


class MemoryPluginStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], bool] = {}

    async def get_all(self, user_id: str) -> dict[str, bool]:
        uid = str(user_id)
        return {p: v for (u, p), v in self._rows.items() if u == uid}

    async def set_enabled(self, user_id: str, plugin_id: str, enabled: bool) -> None:
        self._rows[(str(user_id), plugin_id)] = bool(enabled)


class PgPluginStore:
    def __init__(self, get_pg_client=None) -> None:
        self._get_pg_client = get_pg_client or _default_get_pg_client

    async def get_all(self, user_id: str) -> dict[str, bool]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(
                "SELECT plugin_id, enabled FROM user_plugins "
                "WHERE user_id = $1::uuid", str(user_id))
        return {r["plugin_id"]: bool(r["enabled"]) for r in rows}

    async def set_enabled(self, user_id: str, plugin_id: str, enabled: bool) -> None:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            await conn.execute(
                "INSERT INTO user_plugins (user_id, plugin_id, enabled, updated_at) "
                "VALUES ($1::uuid, $2, $3, now()) "
                "ON CONFLICT (user_id, plugin_id) DO UPDATE "
                "SET enabled = EXCLUDED.enabled, updated_at = now()",
                str(user_id), plugin_id, bool(enabled))


# ── Resolution ────────────────────────────────────────────────────────────

_store: Optional[PluginStore] = None


def _default_get_pg_client():
    from langgraph_agents.shared import get_pg_client
    return get_pg_client()


def set_store(store: Optional[PluginStore]) -> None:
    """Install a store explicitly (tests, api/main.py). None resets."""
    global _store
    _store = store


def get_plugin_store() -> PluginStore:
    global _store
    if _store is not None:
        return _store
    if os.getenv("VVA_LOOP_LEASE_STORE", "").strip().lower() == "memory":
        _store = MemoryPluginStore()
    else:
        _store = PgPluginStore()
    return _store
