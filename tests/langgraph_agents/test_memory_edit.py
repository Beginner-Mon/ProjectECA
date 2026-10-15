"""Fact edits preserve identity/provenance and reject stale or foreign rows.

Runs with unittest as well as pytest; the local workstation has no pytest.
No database or authenticated account is used by these tests.
"""
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from langgraph_agents.api.auth import current_user_id
from langgraph_agents.api.routes_crud import router

UID = "00000000-0000-4000-a000-000000000001"
FACT = "00000000-0000-4000-a000-000000000002"
STAMP = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)


class MemoryEditTests(TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[current_user_id] = lambda: UID
        self.client = TestClient(app)
        self.conn = MagicMock()
        self.row = dict(
            id=FACT, fact_text="Updated fact", category="preference", valid=True,
            kind="fact", tier=2, status="active", project=None,
            source="extracted", created_at=STAMP, updated_at=STAMP, said_at=STAMP,
        )
        self.conn.fetchrow = AsyncMock(side_effect=[{"updated_at": STAMP}, self.row])
        pg = MagicMock()
        pg.transaction.return_value.__aenter__ = AsyncMock(return_value=self.conn)
        pg.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        self.mock_pg = patch("langgraph_agents.api.routes_crud.get_pg_client", return_value=pg)
        self.mock_pg.start()
        self.addCleanup(self.mock_pg.stop)
        self.addCleanup(self.client.close)

    def edit(self, **overrides):
        body = {"fact_text": "  Updated fact  ", "expected_updated_at": STAMP.isoformat()}
        body.update(overrides)
        return self.client.post(f"/me/memory/{FACT}", json=body)

    def test_updates_only_text_and_timestamp_with_owner_scope(self):
        response = self.edit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], FACT)
        self.assertEqual(response.json()["source"], "extracted")
        self.assertEqual(response.json()["category"], "preference")
        reads, writes = self.conn.fetchrow.await_args_list
        self.assertIn("FOR UPDATE", reads.args[0])
        for call in (reads, writes):
            self.assertIn("user_id = $2::uuid", call.args[0])
            self.assertIn("valid = true", call.args[0])
            self.assertEqual(str(call.args[1]), FACT)
            self.assertEqual(call.args[2], UID)
        self.assertEqual(writes.args[3], "Updated fact")
        assignments = writes.args[0].split("SET", 1)[1].split("WHERE", 1)[0]
        self.assertNotIn("source", assignments)
        self.assertNotIn("category", assignments)
        self.assertNotIn("created_at", assignments)
        self.assertIn("said_at = now()", assignments)
        self.assertIn("updated_at = now()", assignments)

    def test_stale_edit_returns_conflict_without_writing(self):
        response = self.edit(expected_updated_at="2026-10-14T07:00:00+00:00")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.conn.fetchrow.await_count, 1)

    def test_extractor_write_between_list_and_save_returns_conflict(self):
        """The extractor stamps updated_at on every write: a user saving over
        a row the extractor just refreshed gets 409, which is correct."""
        self.conn.fetchrow.side_effect = [{"updated_at": datetime(
            2026, 10, 14, 8, 0, 1, tzinfo=timezone.utc)}]
        response = self.edit()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.conn.fetchrow.await_count, 1)

    def test_new_fact_returns_version_for_immediate_edit(self):
        self.conn.execute = AsyncMock()
        self.conn.fetchrow.side_effect = [self.row]
        response = self.client.post('/me/memory', json={"fact_text": "New fact"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["updated_at"])

    def test_missing_deleted_or_other_users_fact_is_not_written(self):
        self.conn.fetchrow.side_effect = [None]
        self.assertEqual(self.edit().status_code, 404)
        self.assertEqual(self.conn.fetchrow.await_count, 1)

    def test_equivalent_timezone_offset_is_accepted(self):
        self.assertEqual(self.edit(expected_updated_at="2026-10-14T15:00:00+07:00").status_code, 200)

    def test_blank_or_oversized_edits_never_reach_database(self):
        for text in (" ", "\n\t", "x" * 501):
            with self.subTest(text=text[:10]):
                self.assertEqual(self.edit(fact_text=text).status_code, 422)
        self.conn.fetchrow.assert_not_awaited()

    def test_provenance_cannot_be_changed_by_request(self):
        for field, value in (("source", "user"), ("user_id", UID), ("category", "other")):
            with self.subTest(field=field):
                self.assertEqual(self.edit(**{field: value}).status_code, 422)
        self.conn.fetchrow.assert_not_awaited()

    def test_bad_id_and_missing_version_are_rejected(self):
        self.assertEqual(self.client.post("/me/memory/not-an-id", json={
            "fact_text": "x", "expected_updated_at": STAMP.isoformat(),
        }).status_code, 422)
        self.assertEqual(self.client.post(f"/me/memory/{FACT}", json={"fact_text": "x"}).status_code, 422)
        self.conn.fetchrow.assert_not_awaited()

    def test_missing_token_is_rejected(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.post(f"/me/memory/{FACT}", json={
                "fact_text": "x", "expected_updated_at": STAMP.isoformat(),
            })
        self.assertEqual(response.status_code, 401)
        self.conn.fetchrow.assert_not_awaited()


class MemoryTierFieldsTests(TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[current_user_id] = lambda: UID
        self.client = TestClient(app)
        self.pg = MagicMock()
        self.mock_pg = patch("langgraph_agents.api.routes_crud.get_pg_client",
                             return_value=self.pg)
        self.mock_pg.start()
        self.addCleanup(self.mock_pg.stop)
        self.addCleanup(self.client.close)

    def _row(self, **over):
        row = dict(id=FACT, fact_text="t", category="goal", valid=True,
                   kind="fact", tier=2, status="active", project=None,
                   source="user", created_at=STAMP, updated_at=STAMP,
                   said_at=STAMP)
        row.update(over)
        return row

    def test_create_instruction_lands_tier1_and_create_fact_tier2(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=self._row())
        self.pg.transaction.return_value.__aenter__ = AsyncMock(return_value=conn)
        self.pg.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        response = self.client.post("/me/memory", json={
            "fact_text": "Answer briefly", "kind": "instruction", "project": "work"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["kind"], body["tier"], body["status"],
                          body["project"]), ("instruction", 1, "active", "work"))
        insert = conn.fetchrow.await_args.args[0]
        self.assertIn("kind, tier", " ".join(insert.split()))
        self.assertIn("'user'", " ".join(insert.split()))

        response = self.client.post("/me/memory", json={"fact_text": "Likes pho"})
        body = response.json()
        self.assertEqual((body["kind"], body["tier"]), ("fact", 2))
        self.assertIsNone(body["project"])

    def test_create_rejects_bad_kind_and_long_project(self):
        self.assertEqual(self.client.post(
            "/me/memory", json={"fact_text": "x", "kind": "note"}).status_code, 422)
        self.assertEqual(self.client.post(
            "/me/memory",
            json={"fact_text": "x", "project": "p" * 61}).status_code, 422)

    def test_list_returns_tier_fields_and_candidates(self):
        self.pg.connect = AsyncMock()
        self.pg.fetch = AsyncMock(return_value=[
            self._row(id="a", kind="instruction", tier=1),
            self._row(id="b", status="candidate", said_at=None),
        ])
        response = self.client.get("/me/memory")
        self.assertEqual(response.status_code, 200)
        facts = {f["id"]: f for f in response.json()["facts"]}
        self.assertEqual(facts["a"]["tier"], 1)
        self.assertEqual(facts["b"]["status"], "candidate")
        self.assertIsNone(facts["b"]["said_at"])
        self.assertEqual(facts["a"]["said_at"], STAMP.isoformat())
        select = self.pg.fetch.await_args.args[0]
        self.assertIn("kind", select)
        self.assertIn("tier", select)
        self.assertIn("said_at", select)
        self.assertIn("valid = true", select)

    def test_delete_all_returns_the_count(self):
        self.pg.connect = AsyncMock()
        self.pg.execute = AsyncMock(return_value="DELETE 3")
        response = self.client.delete("/me/memory")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 3})
        self.assertIn("DELETE FROM user_memory", self.pg.execute.await_args.args[0])
        self.assertNotIn("source", self.pg.execute.await_args.args[0])
