"""Guards on migration 007_rls.py, and the real thing against the database.

The static tests exist because the two mistakes this migration can make are both
silent. A policy written with `current_setting('app.user_id', true)` returns an
empty account instead of an error; a missing WITH CHECK lets a user write rows
they cannot read back. Neither shows up as a failure anywhere else.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "agenticRAG" / "langgraph_agents" / "alembic" / "versions" / "007_rls.py"
)

USER_TABLES = ("conversations", "user_memory", "billing_accounts", "users",
               "messages", "summaries")


def _statements(direction: str = "upgrade") -> list[str]:
    """Run the migration with op.execute captured, and return the SQL.

    Reading the file as text does not work here: the migration builds its
    statements with f-strings in a loop, so `GRANT SELECT ON "characters"` never
    appears literally. Worse, a text search matches the prose — an earlier
    version of this file failed its own no-ALTER-DEFAULT-PRIVILEGES check
    against the comment explaining why there is none.

    Executing it against a fake `op` tests what actually runs.
    """
    spec = importlib.util.spec_from_file_location("migration_007", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    captured: list[str] = []
    with patch.object(module.op, "execute", side_effect=captured.append):
        getattr(module, direction)()
    return captured


# ── Guards on the generated SQL ───────────────────────────────────────────────


@pytest.mark.unit
def test_current_setting_has_no_missing_ok_argument():
    """`current_setting('app.user_id')` must take exactly one argument.

    With a second argument of true, an unset GUC yields NULL, `user_id = NULL`
    is NULL, and the row is filtered out — so a query that forgot user_scope()
    quietly reports that the user has nothing. Without it, PostgreSQL raises and
    the mistake is visible the first time it runs.
    """
    offenders = [
        sql for sql in _statements()
        if re.search(r"current_setting\(\s*'app\.user_id'\s*,", sql)
    ]
    assert not offenders, (
        "current_setting('app.user_id') was given a second argument: "
        f"{offenders}. That turns a forgotten user_scope() into an empty result "
        "instead of an error. Drop the argument."
    )


@pytest.mark.unit
def test_every_user_table_gets_rls_and_a_policy():
    sql = "\n".join(_statements())
    for table in USER_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in sql, (
            f"{table} holds user-owned rows but RLS is never enabled on it"
        )
        assert f"CREATE POLICY {table}_owner" in sql, (
            f"{table} has RLS enabled but no policy — which denies everything"
        )


@pytest.mark.unit
def test_policies_constrain_writes_as_well_as_reads():
    """USING filters reads; WITH CHECK constrains writes. Both are needed.

    A policy with only USING lets a user INSERT a row owned by somebody else —
    the write succeeds and the row then becomes invisible to its author, which
    reads as data loss rather than as a permissions bug.
    """
    policies = [s for s in _statements() if s.startswith("CREATE POLICY")]
    assert len(policies) == len(USER_TABLES), (
        f"{len(policies)} policies for {len(USER_TABLES)} tables"
    )
    for policy in policies:
        assert "USING (" in policy, f"policy without USING: {policy[:80]}"
        assert "WITH CHECK (" in policy, (
            f"policy without WITH CHECK — writes are unconstrained: {policy[:80]}"
        )


@pytest.mark.unit
def test_no_default_privileges_grant():
    """A table added later must be granted on purpose.

    ALTER DEFAULT PRIVILEGES would make every future table readable by the
    application the moment somebody creates it — including one holding data
    nobody meant to expose.
    """
    offenders = [s for s in _statements() if "ALTER DEFAULT PRIVILEGES" in s]
    assert not offenders, (
        f"grants on tables that do not exist yet: {offenders}"
    )


@pytest.mark.unit
def test_system_tables_are_read_only_for_the_app():
    """characters and kb_embeddings belong to nobody; writes stay with the owner."""
    grants = [s for s in _statements() if s.startswith("GRANT")]
    for table in ("characters", "kb_embeddings"):
        on_table = [g for g in grants if f'"{table}"' in g]
        assert on_table, f"{table} is never granted to the application role"
        for grant in on_table:
            for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert verb not in grant, (
                    f"{table} is reference data, but the app is granted {verb}: {grant}"
                )


@pytest.mark.unit
def test_downgrade_removes_what_upgrade_added():
    """A migration that cannot be undone is a migration you cannot deploy twice."""
    down = "\n".join(_statements("downgrade"))
    for table in USER_TABLES:
        assert f"DROP POLICY IF EXISTS {table}_owner" in down
        assert f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY' in down


# ── Against the database ──────────────────────────────────────────────────────


@pytest.fixture
def app_dsn_or_skip():
    """The DSN the application uses — which must not be the owner's.

    VVA_PG_DSN means "what the application connects as", and since 007 that is
    eca_user. The owner credential lives under VVA_PG_DSN_OWNER and is Alembic's.

    The guard below is the point of the fixture: the owner bypasses row-level
    security, so running these tests as the owner would pass every one of them
    while proving nothing.
    """
    import os
    from urllib.parse import urlsplit

    from langgraph_agents.shared.env import load_env

    load_env()
    dsn = os.getenv("VVA_PG_DSN")
    if not dsn:
        pytest.skip("VVA_PG_DSN not configured")

    role = urlsplit(dsn).username or ""
    if role.endswith("owner") or role == "postgres":
        pytest.fail(
            f"VVA_PG_DSN connects as {role!r}, which bypasses RLS. These tests "
            f"would pass without testing anything. Point it at the application "
            f"role and keep the owner in VVA_PG_DSN_OWNER."
        )
    return dsn


@pytest.mark.integration
@pytest.mark.asyncio
async def test_app_role_is_not_privileged(app_dsn_or_skip):
    """The role must not be able to step around its own policies.

    Checked first because everything below passes vacuously otherwise: a role
    with BYPASSRLS, or one inheriting pg_read_all_data through a group, reads
    every row while the policies sit there looking correct.
    """
    import asyncpg

    conn = await asyncpg.connect(app_dsn_or_skip)
    try:
        row = await conn.fetchrow(
            "SELECT rolbypassrls, rolsuper, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname = current_user"
        )
        assert not row["rolbypassrls"], "the application role has BYPASSRLS"
        assert not row["rolsuper"], "the application role is a superuser"
        assert not row["rolcreaterole"], "the application role can create roles"
        assert not row["rolcreatedb"], "the application role can create databases"

        groups = await conn.fetch(
            "SELECT g.rolname FROM pg_auth_members am "
            "JOIN pg_roles m ON m.oid = am.member "
            "JOIN pg_roles g ON g.oid = am.roleid "
            "WHERE m.rolname = current_user"
        )
        assert not groups, (
            f"the application role belongs to {[g[0] for g in groups]}; "
            "neon_superuser in particular carries pg_read_all_data"
        )
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_query_without_user_scope_raises(app_dsn_or_skip):
    """Forgetting user_scope() must be an error, not an empty result."""
    import asyncpg

    conn = await asyncpg.connect(app_dsn_or_skip)
    try:
        with pytest.raises(asyncpg.PostgresError) as exc_info:
            await conn.fetchval("SELECT count(*) FROM conversations")
        assert "app.user_id" in str(exc_info.value)
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_app_role_cannot_write_system_tables(app_dsn_or_skip):
    import asyncpg

    conn = await asyncpg.connect(app_dsn_or_skip)
    try:
        await conn.fetchval("SELECT count(*) FROM characters")     # read: allowed
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM characters WHERE slug = 'nonexistent'")
    finally:
        await conn.close()


# ── Guards on migration 013_character_knowledge (plan T8b) ─────────────────


_MIGRATION_013 = (
    Path(__file__).resolve().parents[2]
    / "agenticRAG" / "langgraph_agents" / "alembic" / "versions"
    / "013_character_knowledge.py"
)


def _statements_013(direction: str = "upgrade") -> list[str]:
    spec = importlib.util.spec_from_file_location("migration_013", _MIGRATION_013)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "012_message_feedback", (
        f"013 must revise 012_message_feedback, got {module.down_revision!r}"
    )

    captured: list[str] = []
    with patch.object(module.op, "execute", side_effect=captured.append):
        getattr(module, direction)()
    return captured


@pytest.mark.unit
def test_013_character_policy_has_no_missing_ok_argument():
    """`current_setting('app.character')` takes exactly one argument.

    With a second argument of true, a turn that forgot bind_request_character()
    would read NOBODY's sheet instead of erroring — or worse, silently match
    nothing and let the model invent the character. Same rule as 007.
    """
    offenders = [
        sql for sql in _statements_013()
        if re.search(r"current_setting\(\s*'app\.character'\s*,", sql)
    ]
    assert not offenders, (
        "current_setting('app.character') was given a second argument: "
        f"{offenders}. Drop the argument."
    )


@pytest.mark.unit
def test_013_character_knowledge_rls_and_select_only():
    sql = "\n".join(_statements_013())
    assert 'ALTER TABLE "character_knowledge" ENABLE ROW LEVEL SECURITY' in sql
    assert "CREATE POLICY character_knowledge_owner" in sql
    assert "FOR SELECT" in sql
    grants = [s for s in _statements_013() if s.startswith("GRANT")]
    on_table = [g for g in grants if '"character_knowledge"' in g]
    assert on_table, "character_knowledge is never granted to the application role"
    for grant in on_table:
        for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert verb not in grant, (
                f"character_knowledge is reference data, but the app is granted {verb}: {grant}"
            )


@pytest.mark.unit
def test_013_downgrade_removes_what_upgrade_added():
    down = "\n".join(_statements_013("downgrade"))
    assert "DROP POLICY IF EXISTS character_knowledge_owner" in down
    assert 'ALTER TABLE IF EXISTS "character_knowledge" DISABLE ROW LEVEL SECURITY' in down
    assert "DROP TABLE IF EXISTS character_knowledge" in down


# ── Against the database (needs H3: migration 013 on Neon) ─────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_013_character_isolation_live(app_dsn_or_skip):
    """anne không đọc được dòng của nhân vật khác; quên bind thì lỗi.

    Seed bằng slug giả (__test_a/__test_b) dưới quyền owner rồi xóa sạch —
    không động vào dữ liệu thật (Tri duyệt hình thức này).
    """
    import asyncpg

    import os

    owner_dsn = os.getenv("VVA_PG_DSN_OWNER")
    if not owner_dsn:
        pytest.skip("VVA_PG_DSN_OWNER not configured")

    vec = "[" + ",".join(["0.0"] * 384) + "]"
    owner = await asyncpg.connect(owner_dsn)
    try:
        for slug in ("__test_a", "__test_b"):
            await owner.execute(
                "INSERT INTO character_knowledge "
                "(character_slug, kind, title, content, chunk_index, embedding) "
                "VALUES ($1, 'sheet', 'T', 'c', 0, $2::vector)",
                slug, vec,
            )

        app = await asyncpg.connect(app_dsn_or_skip)
        try:
            # Không bind → lỗi (policy không có missing_ok), không phải 0 dòng.
            with pytest.raises(asyncpg.PostgresError):
                await app.fetch("SELECT count(*) FROM character_knowledge")

            # Bind __test_a → chỉ thấy dòng __test_a.
            await app.execute("SELECT set_config('app.character', '__test_a', false)")
            rows = await app.fetch(
                "SELECT DISTINCT character_slug FROM character_knowledge")
            assert [r["character_slug"] for r in rows] == ["__test_a"]

            # Bind nhân vật khác → không thấy dòng __test_a.
            await app.execute("SELECT set_config('app.character', '__test_b', false)")
            rows = await app.fetch(
                "SELECT DISTINCT character_slug FROM character_knowledge")
            assert [r["character_slug"] for r in rows] == ["__test_b"]
        finally:
            await app.close()
    finally:
        await owner.execute(
            "DELETE FROM character_knowledge WHERE character_slug IN ('__test_a', '__test_b')")
        await owner.close()
