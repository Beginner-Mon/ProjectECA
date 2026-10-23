"""Fact extraction (memory v2, C1b): the switches, housekeep, reading, the checks, the writes.

No database and no real LLM here: the client and the model are fakes. What the
SQL actually selects and what the policies allow is pinned against a real
Postgres in test_fact_extractor_integration.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.nodes import fact_extractor as fe
from langgraph_agents.nodes import summarizer as summ
from langgraph_agents.shared.logging import JsonFormatter

pytestmark = pytest.mark.unit

_USER = "11111111-1111-1111-1111-111111111111"
_CURRENT = "22222222-2222-2222-2222-222222222222"
_OTHER = "33333333-3333-3333-3333-333333333333"
_AT = datetime(2026, 10, 12, 23, 30, tzinfo=timezone.utc)


# ── The switches ──────────────────────────────────────────────────────────────


def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("VVA_LTM", raising=False)
    with patch.object(fe, "_memory_config", return_value={}):
        assert fe.enabled() is False


def test_switch_reads_the_yaml_when_the_env_is_unset(monkeypatch):
    monkeypatch.delenv("VVA_LTM", raising=False)
    with patch.object(fe, "_memory_config", return_value={"ltm_enabled": True}):
        assert fe.enabled() is True


@pytest.mark.parametrize("env, yaml_value, expected", [
    ("1", False, True), ("0", True, False), ("1", True, True), ("0", False, False),
])
def test_env_beats_yaml(monkeypatch, env, yaml_value, expected):
    monkeypatch.setenv("VVA_LTM", env)
    with patch.object(fe, "_memory_config", return_value={"ltm_enabled": yaml_value}):
        assert fe.enabled() is expected


def test_an_env_value_that_is_neither_0_nor_1_falls_back_to_the_yaml(monkeypatch):
    monkeypatch.setenv("VVA_LTM", "yes")
    with patch.object(fe, "_memory_config", return_value={"ltm_enabled": False}):
        assert fe.enabled() is False


def test_policy_thresholds_default_to_the_plan_values():
    with patch.object(fe, "_memory_config", return_value={}):
        assert fe._policy_defaults() == {"forget_purge_days": 30}
    with patch.object(fe, "_memory_config", return_value={"forget_purge_days": 7}):
        policy = fe._policy_defaults()
        assert policy["forget_purge_days"] == 7


def test_the_shipped_yaml_has_extraction_on(monkeypatch):
    """Demo default (17/10): extraction on; `VVA_LTM=0` on Lambda is the off switch."""
    monkeypatch.delenv("VVA_LTM", raising=False)
    assert fe.enabled() is True


# ── housekeep ─────────────────────────────────────────────────────────────────


def _housekeep_patches(ltm_on, extract_error=None):
    calls = []

    def step(name, error=None):
        async def run(*_args, **_kwargs):
            calls.append(name)
            if error is not None:
                raise error
            return 0
        return AsyncMock(side_effect=run)

    extract = step("extract", extract_error)
    return calls, extract, [
        patch.object(summ, "compact_stm", new=step("compact")),
        patch.object(fe, "enabled", return_value=ltm_on),
        patch.object(fe, "extract_user_facts", new=extract),
    ]


async def _housekeep(patches, **kwargs):
    for p in patches:
        p.start()
    try:
        await summ.housekeep("user-1", "s-1", **kwargs)
    finally:
        patch.stopall()


@pytest.mark.asyncio
async def test_housekeep_with_the_switch_off_never_calls_the_extractor():
    calls, extract, patches = _housekeep_patches(False)
    await _housekeep(patches, history_budget=5, compact_at=9)
    assert calls == ["compact"]
    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_housekeep_with_the_switch_on_extracts_alongside_compaction():
    calls, extract, patches = _housekeep_patches(True)
    await _housekeep(patches)
    assert sorted(calls) == ["compact", "extract"]
    extract.assert_awaited_once_with("user-1", "s-1")


@pytest.mark.asyncio
async def test_housekeep_survives_an_extraction_error_and_logs_its_type_only(caplog):
    calls, _, patches = _housekeep_patches(
        True, extract_error=RuntimeError("Failing row contains (FACTSECRET)"))
    with caplog.at_level(logging.DEBUG):
        await _housekeep(patches)
    assert sorted(calls) == ["compact", "extract"]
    (failed,) = [r for r in caplog.records if r.getMessage() == "fact_extraction_failed"]
    assert failed.levelno == logging.WARNING
    assert failed.error == "RuntimeError" and failed.session_id == "s-1"
    assert "FACTSECRET" not in _formatted(caplog.records)


# ── Fakes ─────────────────────────────────────────────────────────────────────


def _msg(seq, role, content, session=_CURRENT, marker=0, at=_AT):
    return {"session_id": session, "ltm_up_to_seq": marker, "seq_id": seq,
            "role": role, "content": content, "created_at": at}


def _fact(text, category="condition", source="extracted", fact_id=None,
          kind="fact", tier=2, status="active", project=None):
    return {"id": fact_id or uuid.uuid4(), "fact_text": text,
            "category": category, "source": source, "kind": kind, "tier": tier,
            "status": status, "project": project, "seen_count": 1,
            "seen_projects": [project or ""],
            "last_seen_session_id": None, "said_at": _AT}


_TAGS = {"_MARK_SQL": "UPDATE 1", "_ADD_SQL": "INSERT 0 1",
         "_UPDATE_SQL": "UPDATE 1", "_REPEAT_SQL": "UPDATE 1",
         "_HIDE_SQL": "UPDATE 1", "_FORGET_ALL_SQL": "UPDATE 5",
         "_PURGE_SQL": "DELETE 0"}


def _name(sql):
    for name in ("_UNREAD_SQL", "_TIER1_SQL", "_TIER2_SQL", "_PROJECTS_SQL",
                 *_TAGS):
        if sql is getattr(fe, name):
            return name
    raise AssertionError(f"a statement this module does not own: {sql!r}")


class _Conn:
    def __init__(self, tags):
        self.tags = tags
        self.statements: list[tuple] = []

    async def execute(self, sql, *args):
        name = _name(sql)
        self.statements.append((name, args))
        return self.tags[name]


class _Pg:
    """Records every call; `calls` is the order the client was used in."""

    def __init__(self, unread, facts=(), tags=None):
        self.unread, self.facts = list(unread), list(facts)
        self.tags = {**_TAGS, **(tags or {})}
        self.calls: list[tuple] = []
        self.transactions: list[_Conn] = []

    async def fetch(self, sql, *args):
        name = _name(sql)
        self.calls.append((name, args))
        if name == "_UNREAD_SQL":
            return self.unread
        if name == "_TIER1_SQL":
            return [f for f in self.facts if f["tier"] == 1]
        if name == "_TIER2_SQL":
            return [f for f in self.facts if f["tier"] == 2]
        return [{"project": p} for p in
                sorted({f["project"] for f in self.facts if f["project"]})]

    async def execute(self, sql, *args):
        name = _name(sql)
        self.calls.append((name, args))
        return self.tags[name]

    @asynccontextmanager
    async def transaction(self):
        conn = _Conn(self.tags)
        self.transactions.append(conn)
        self.calls.append(("transaction", ()))
        yield conn

    def names(self):
        return [name for name, _ in self.calls]


def _llm(*answers):
    """A model whose calls return `answers` in turn: a list of operations, an
    exception to raise, or None for an answer that could not be parsed."""
    replies = []
    for answer in answers:
        if isinstance(answer, BaseException):
            replies.append(answer)
        elif answer is None:
            replies.append({"raw": SimpleNamespace(content="RAWSECRET not json"),
                            "parsed": None,
                            "parsing_error": ValueError("Got: RAWSECRET not json")})
        else:
            replies.append({"raw": SimpleNamespace(content="RAWSECRET"),
                            "parsed": fe.Operations(operations=answer),
                            "parsing_error": None})
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=replies)
    return llm


async def _run(pg, llm=None):
    llm = llm or _llm()
    with patch.object(fe, "get_pg_client", return_value=pg), \
         patch.object(fe, "extraction_llm", return_value=llm):
        summary = await fe.extract_user_facts(_USER, _CURRENT)
    return summary, llm


def _user_message(llm, call=0):
    (system, user), = llm.ainvoke.await_args_list[call].args
    assert system == ("system", fe.MEMORY_PROMPT)
    assert user[0] == "user"
    return user[1]


def _tier_in(user_message, tier):
    block = user_message.split("Stored items (newest first):\n", 1)[1]
    block = block.split("\n\nKnown topics by type:", 1)[0]
    if tier == 1:
        return "(none)"
    return block


def _stored_ids(user_message, tier):
    import json as _json
    block = _tier_in(user_message, tier)
    if block == "(none)":
        return []
    return [_json.loads(line)["id"] for line in block.split("\n")]


def _formatted(records):
    return "\n".join(JsonFormatter().format(r) for r in records)


def _done(records):
    return [r for r in records if r.getMessage() == "fact_extraction_done"]


# ── Which conversations ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_read_selects_the_conversations_and_their_unread_messages():
    pg = _Pg([])
    summary, llm = await _run(pg)
    assert pg.calls == [("_UNREAD_SQL", (_USER, fe._MAX_CONVERSATIONS,
                                          fe._SLICE_MAX_ROWS)),
                         ("_PURGE_SQL", (_USER, 30))]
    assert fe._MAX_CONVERSATIONS == 2
    llm.ainvoke.assert_not_awaited()
    assert summary["conversations"] == 0 and summary["llm_calls"] == 0
    assert summary["purged"] == 0


def test_the_read_takes_unread_conversations_most_recently_updated_first():
    sql = " ".join(fe._UNREAD_SQL.split())
    assert "c.user_id = $1" in sql
    assert "ORDER BY c.updated_at DESC" in sql
    assert "m.seq_id > c.ltm_up_to_seq" in sql and "EXISTS" in sql
    assert "LIMIT $2" in sql
    # Each conversation's messages oldest first, a bounded number of them.
    assert "ORDER BY m.seq_id LIMIT $3" in sql


@pytest.mark.asyncio
async def test_conversations_are_processed_in_the_order_read_each_from_its_own_marker():
    pg = _Pg([
        _msg(41, "user", "current one", _CURRENT, marker=40),
        _msg(7, "user", "older one", _OTHER, marker=3),
        _msg(8, "assistant", "its answer", _OTHER, marker=3),
    ])
    summary, llm = await _run(pg, _llm([], []))
    assert "current one" in _user_message(llm, 0) and "older one" not in _user_message(llm, 0)
    assert "older one" in _user_message(llm, 1) and "its answer" in _user_message(llm, 1)
    marks = [args for conn in pg.transactions for name, args in conn.statements
             if name == "_MARK_SQL"]
    assert marks == [(_CURRENT, _USER, 40, 41), (_OTHER, _USER, 3, 8)]
    assert summary["conversations"] == 2 and summary["llm_calls"] == 2


# ── What is read from one conversation ────────────────────────────────────────


def test_slice_is_oldest_first_and_stops_before_the_message_that_would_pass_the_cap():
    rows = [_msg(1, "user", "a" * 3000), _msg(2, "assistant", "b" * 4000),
            _msg(3, "user", "c" * 1001), _msg(4, "assistant", "d" * 10)]
    taken = fe._take_slice(rows)
    assert [m["seq_id"] for m in taken] == [1, 2]
    assert all(m["content"] == r["content"] for m, r in zip(taken, rows))
    assert fe._SLICE_MAX_CHARS == 8000


def test_slice_takes_a_message_that_lands_exactly_on_the_cap():
    rows = [_msg(1, "user", "a" * 4000), _msg(2, "assistant", "b" * 4000),
            _msg(3, "user", "c")]
    assert [m["seq_id"] for m in fe._take_slice(rows)] == [1, 2]


def test_slice_always_takes_at_least_one_message_whole():
    rows = [_msg(1, "user", "a" * 20_000), _msg(2, "assistant", "b")]
    taken = fe._take_slice(rows)
    assert [m["seq_id"] for m in taken] == [1]
    assert len(taken[0]["content"]) == 20_000


@pytest.mark.asyncio
async def test_the_marker_only_advances_to_the_last_message_read():
    pg = _Pg([_msg(5, "user", "a" * 6000, marker=4), _msg(6, "assistant", "b" * 6000, marker=4),
              _msg(7, "user", "c", marker=4)])
    _, llm = await _run(pg, _llm([]))
    assert "b" * 6000 not in _user_message(llm)
    assert pg.transactions[0].statements == [("_MARK_SQL", (_CURRENT, _USER, 4, 5))]


# ── The gate ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_slice_with_no_user_message_costs_one_read_and_one_write(caplog):
    pg = _Pg([_msg(12, "assistant", "the previous answer", marker=11)])
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        summary, llm = await _run(pg)
    llm.ainvoke.assert_not_awaited()
    assert pg.calls == [
        ("_UNREAD_SQL", (_USER, fe._MAX_CONVERSATIONS, fe._SLICE_MAX_ROWS)),
        ("_MARK_SQL", (_CURRENT, _USER, 11, 12)),
        ("_PURGE_SQL", (_USER, 30)),
    ]
    assert summary == {"conversations": 1, "llm_calls": 0, "failed": 0,
                       "added": 0, "updated": 0, "deleted": 0, "repeated": 0,
                       "purged": 0}
    (done,) = _done(caplog.records)
    assert done.llm_called is False and done.message_count == 1


# ── The call ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_model_gets_the_date_the_stored_facts_and_the_labelled_messages():
    typed = _fact("Prefers low-impact exercise", "preference", source="user")
    extracted = _fact("Has lower back pain", "condition")
    pg = _Pg(
        [_msg(8, "assistant", "How is your back?", marker=7,
              at=datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)),
         _msg(9, "user", "My back is fine now", marker=7,
              at=datetime(2026, 10, 12, 23, 30, tzinfo=timezone.utc))],
        facts=[typed, extracted])
    _, llm = await _run(pg, _llm([]))

    assert pg.names()[:4] == ["_UNREAD_SQL", "_TIER1_SQL", "_TIER2_SQL",
                              "_PROJECTS_SQL"]
    assert pg.calls[2] == ("_TIER2_SQL", (_USER, 200))
    message = _user_message(llm)
    assert message.startswith("Conversation date: 2026-10-12\n")
    assert _stored_ids(message, 1) == []
    assert _stored_ids(message, 2) == ["f1", "f2"]
    assert '"locked": true' in _tier_in(message, 2)
    assert "[assistant] How is your back?\n[user] My back is fine now" in message
    assert message.endswith("Reply with the JSON object only.")
    # No database id reaches the model: it answers with the short ones.
    assert str(typed["id"]) not in message and str(extracted["id"]) not in message


@pytest.mark.asyncio
async def test_the_scope_line_says_whether_the_conversation_is_read_from_its_start():
    _, from_start = await _run(_Pg([_msg(1, "user", "hello", marker=0)]), _llm([]))
    assert "Messages to read (a whole conversation):" in _user_message(from_start)
    _, later = await _run(_Pg([_msg(9, "user", "hello", marker=8)]), _llm([]))
    assert "Messages to read (a whole conversation)" not in _user_message(later)
    assert "Messages to read (part of a conversation" in _user_message(later)


def test_the_stored_facts_read_is_the_users_valid_facts_newest_first():
    tier1 = " ".join(fe._TIER1_SQL.split())
    assert "WHERE user_id = $1 AND valid = true AND tier = 1" in tier1
    assert "safety" not in tier1
    assert "ORDER BY said_at DESC" in tier1
    assert "safety" not in " ".join(fe._TIER2_SQL.split())
    tier2 = " ".join(fe._TIER2_SQL.split())
    assert "WHERE user_id = $1 AND valid = true AND tier = 2" in tier2
    assert "ORDER BY said_at DESC" in tier2 and "LIMIT $2" in tier2
    assert fe._MAX_TIER2_SHOWN == 200


def test_the_model_is_the_memory_role_answering_in_json_mode():
    model = MagicMock()
    with patch.object(fe, "get_chat_model", return_value=model) as get:
        structured = fe.extraction_llm()
    get.assert_called_once_with("memory")
    model.with_structured_output.assert_called_once_with(
        fe.Operations, method="json_mode", include_raw=True)
    assert structured is model.with_structured_output.return_value


# ── Applying the result ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_update_repeat_delete_and_the_marker_go_in_one_transaction(caplog):
    back = _fact("Has lower back pain", "condition")
    knee = _fact("Left knee hurts after 10 minutes of walking", "condition")
    pg = _Pg([_msg(21, "assistant", "noted", marker=20), _msg(22, "user", "an update", marker=20)],
             facts=[back, knee])
    ops = [
        {"op": "ADD", "type": "area", "topic": "running", "fact": " Wants to run 5 km by spring "},
        {"op": "UPDATE", "id": "f2", "type": "area", "topic": "knee rehab", "fact": "Left knee is pain-free when walking"},
        {"op": "REPEAT", "id": "f1"},
        {"op": "DELETE", "id": "f1"},
    ]
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        summary, _ = await _run(pg, _llm(ops))

    assert pg.names() == ["_UNREAD_SQL", "_TIER1_SQL", "_TIER2_SQL",
                          "_PROJECTS_SQL", "transaction", "_PURGE_SQL"]
    (conn,) = pg.transactions
    assert conn.statements == [
        ("_MARK_SQL", (_CURRENT, _USER, 20, 22)),
        ("_ADD_SQL", (_USER, "Wants to run 5 km by spring", "area", "fact",
                      2, "active", "running", 1, [""], _CURRENT)),
        ("_UPDATE_SQL", (knee["id"], _USER, "Left knee is pain-free when walking",
                         "area", "knee rehab", "fact", 2, "active", _CURRENT)),
        ("_REPEAT_SQL", (back["id"], _USER, 2, ["", ""], _CURRENT)),
        ("_HIDE_SQL", (back["id"], _USER)),
    ]
    assert summary == {"conversations": 1, "llm_calls": 1, "failed": 0,
                       "added": 1, "updated": 1, "deleted": 1, "repeated": 1,
                       "purged": 0}
    (done,) = _done(caplog.records)
    assert (done.added, done.updated, done.deleted, done.repeated) == (1, 1, 1, 1)
    assert done.session_id == _CURRENT and done.llm_called is True
    assert done.message_count == 2 and done.char_count == len("noted") + len("an update")
    assert done.dropped == {} and done.elapsed_ms >= 0


def test_the_writes_are_scoped_to_the_user_and_to_extracted_facts():
    stmts = {name: " ".join(getattr(fe, name).split()) for name in (
        "_ADD_SQL", "_UPDATE_SQL", "_REPEAT_SQL", "_HIDE_SQL",
        "_FORGET_ALL_SQL", "_PURGE_SQL", "_MARK_SQL")}
    add = stmts["_ADD_SQL"]
    assert "INSERT INTO user_memory" in add and "'extracted'" in add
    assert "source_session_id" in add and "last_seen_session_id" in add
    assert "said_at, updated_at" in add
    for name in ("_UPDATE_SQL", "_REPEAT_SQL"):
        sql = stmts[name]
        assert sql.startswith("UPDATE user_memory SET")
        assert "WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted'" in sql
        assert "updated_at = now()" in sql
    hide = stmts["_HIDE_SQL"]
    assert hide.startswith("UPDATE user_memory SET")
    assert "WHERE id = $1::uuid AND user_id = $2::uuid" in hide
    assert "source = 'extracted'" not in hide
    assert "valid = false" in hide
    assert "updated_at = now()" in hide
    assert "fact_text = $3" in stmts["_UPDATE_SQL"]
    assert "memory_type = $4" in stmts["_UPDATE_SQL"]
    # Hiding a fact never removes the row; only the purge deletes.
    assert "valid = false" in stmts["_HIDE_SQL"]
    assert "valid = false" in stmts["_FORGET_ALL_SQL"]
    assert "source = 'extracted'" in stmts["_FORGET_ALL_SQL"]
    assert "valid = false" in stmts["_PURGE_SQL"]
    assert not any("DELETE FROM" in stmts[n]
                   for n in ("_ADD_SQL", "_UPDATE_SQL", "_REPEAT_SQL",
                             "_HIDE_SQL", "_MARK_SQL"))
    assert stmts["_MARK_SQL"].startswith("UPDATE conversations SET ltm_up_to_seq = $4")
    assert "session_id = $1::uuid AND user_id = $2::uuid" in stmts["_MARK_SQL"]


@pytest.mark.asyncio
async def test_an_add_identical_to_a_stored_fact_is_skipped(caplog):
    pg = _Pg([_msg(2, "user", "again", marker=1)],
             facts=[_fact("Has lower back pain", source="user")])
    ops = [{"op": "ADD", "type": "profile", "topic": "health", "fact": "Has lower back pain"},
           {"op": "ADD", "type": "profile", "topic": "work", "fact": "Works night shifts"},
           {"op": "ADD", "type": "profile", "topic": "work", "fact": "Works night shifts"}]
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        summary, _ = await _run(pg, _llm(ops))
    adds = [args for name, args in pg.transactions[0].statements if name == "_ADD_SQL"]
    assert adds == [(_USER, "Works night shifts", "profile", "fact",
                     2, "active", "work", 1, [""], _CURRENT)]
    assert summary["added"] == 1
    assert _done(caplog.records)[0].dropped == {"duplicate": 2}


@pytest.mark.asyncio
async def test_update_and_delete_never_reach_a_typed_fact_or_an_unknown_id(caplog):
    typed = _fact("Prefers low-impact exercise", "preference", source="user")
    pg = _Pg([_msg(2, "user", "I do HIIT now", marker=1)], facts=[typed])
    ops = [{"op": "UPDATE", "id": "f1", "type": "preference", "topic": "training", "fact": "Does HIIT"},
           {"op": "DELETE", "id": "f1"},
           {"op": "REPEAT", "id": "f1"},
           {"op": "UPDATE", "id": "f9", "type": "preference", "topic": "training", "fact": "Does HIIT"},
           {"op": "DELETE", "id": str(typed["id"])}]
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        summary, _ = await _run(pg, _llm(ops))
    assert pg.transactions[0].statements == [
        ("_MARK_SQL", (_CURRENT, _USER, 1, 2)),
        ("_HIDE_SQL", (typed["id"], _USER)),
    ]
    assert (summary["updated"], summary["deleted"]) == (0, 1)
    assert _done(caplog.records)[0].dropped == {"locked_id": 2, "unknown_id": 2}


@pytest.mark.asyncio
async def test_the_checks_drop_and_count_what_breaks_a_limit(caplog):
    pg = _Pg([_msg(2, "user", "a lot", marker=1)])
    ops = [
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "x" * 301},
        {"op": "ADD", "type": "hobby", "topic": "health", "fact": "Likes swimming"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "Phone is 0912 345 678"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "Mail me at someone@example.com"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "Had knee surgery on 12/07/2026", "safety": True},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "   "},
        {"op": "ADD", "type": "profile", "topic": "one two three four five", "fact": "Long topic"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "Phone is 0912 345 678"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "Tenth operation"},
    ]
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        summary, _ = await _run(pg, _llm(ops))
    adds = [args[1] for name, args in pg.transactions[0].statements if name == "_ADD_SQL"]
    assert adds == ["Had knee surgery on 12/07/2026"]
    assert summary["added"] == 1
    assert _done(caplog.records)[0].dropped == {
        "too_long": 1, "bad_type": 1, "identifier": 3, "empty_fact": 1,
        "bad_topic": 1, "over_limit": 1}


@pytest.mark.asyncio
async def test_a_fact_of_exactly_300_characters_is_kept():
    pg = _Pg([_msg(2, "user", "long", marker=1)])
    summary, _ = await _run(pg, _llm([{"op": "ADD", "type": "profile", "topic": "health", "fact": "y" * 300}]))
    assert summary["added"] == 1 and fe.MAX_FACT_CHARS == 300 and fe.MAX_OPS == 8
    assert fe.MEMORY_TYPES == ("profile", "preference", "area", "event", "person",
                              "reference", "instruction")


@pytest.mark.asyncio
async def test_a_write_that_touches_no_row_is_not_counted_as_applied(caplog):
    gone = _fact("Has lower back pain")
    pg = _Pg([_msg(2, "user", "fine now", marker=1)], facts=[gone],
             tags={"_HIDE_SQL": "UPDATE 0", "_ADD_SQL": "INSERT 0 0"})
    ops = [{"op": "DELETE", "id": "f1"},
           {"op": "ADD", "type": "profile", "topic": "health", "fact": "Stored beyond the sixty shown"}]
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        summary, _ = await _run(pg, _llm(ops))
    assert (summary["added"], summary["deleted"]) == (0, 0)
    assert _done(caplog.records)[0].dropped == {"gone": 1, "duplicate": 1}


@pytest.mark.asyncio
async def test_a_conversation_another_run_already_read_is_left_alone(caplog):
    pg = _Pg([_msg(2, "user", "I had knee surgery", marker=1)], tags={"_MARK_SQL": "UPDATE 0"})
    ops = [{"op": "ADD", "type": "profile", "topic": "health", "fact": "Had knee surgery"}]
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        summary, _ = await _run(pg, _llm(ops))
    assert pg.transactions[0].statements == [("_MARK_SQL", (_CURRENT, _USER, 1, 2))]
    assert summary["added"] == 0
    assert _done(caplog.records)[0].dropped == {"superseded": 1}


def test_the_marker_only_moves_from_where_this_run_started_reading():
    assert "AND ltm_up_to_seq = $3" in " ".join(fe._MARK_SQL.split())


# ── Failure ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, reason, error", [
    (RuntimeError("upstream said: MSGSECRET"), "call_failed", "RuntimeError"),
    (asyncio.TimeoutError(), "call_failed", "TimeoutError"),
    (None, "unparsable", "ValueError"),
])
async def test_a_failed_call_writes_nothing_and_leaves_the_marker(caplog, answer, reason, error):
    pg = _Pg([_msg(2, "user", "MSGSECRET I had knee surgery", marker=1)],
             facts=[_fact("FACTSECRET lower back pain")])
    with caplog.at_level(logging.DEBUG):
        summary, llm = await _run(pg, _llm(answer))
    assert llm.ainvoke.await_count == 1
    assert pg.names() == ["_UNREAD_SQL", "_TIER1_SQL", "_TIER2_SQL", "_PROJECTS_SQL",
                          "_PURGE_SQL"]
    assert pg.transactions == []
    assert summary == {"conversations": 1, "llm_calls": 1, "failed": 1,
                       "added": 0, "updated": 0, "deleted": 0, "repeated": 0,
                       "purged": 0}
    (failed,) = [r for r in caplog.records if r.getMessage() == "fact_extraction_llm_failed"]
    assert failed.levelno == logging.WARNING
    assert (failed.session_id, failed.reason, failed.error) == (_CURRENT, reason, error)
    assert _done(caplog.records) == []
    text = _formatted(caplog.records)
    assert not any(secret in text for secret in ("MSGSECRET", "FACTSECRET", "RAWSECRET"))


@pytest.mark.asyncio
async def test_a_failure_on_one_conversation_does_not_stop_the_next():
    pg = _Pg([_msg(41, "user", "current one", _CURRENT, marker=40),
              _msg(7, "user", "older one", _OTHER, marker=3)])
    summary, llm = await _run(pg, _llm(RuntimeError("boom"), []))
    assert llm.ainvoke.await_count == 2
    assert [conn.statements for conn in pg.transactions] == [
        [("_MARK_SQL", (_OTHER, _USER, 3, 7))]]
    assert summary["failed"] == 1 and summary["conversations"] == 2


@pytest.mark.asyncio
async def test_a_database_error_is_left_to_the_caller():
    pg = _Pg([_msg(2, "user", "hello", marker=1)])
    pg.fetch = AsyncMock(side_effect=ConnectionError("db down"))
    with pytest.raises(ConnectionError):
        await _run(pg)


# ── Logging ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_fact_text_and_no_message_text_reaches_any_log_record(caplog):
    """A full run: a gated conversation, then one with every kind of operation,
    kept and dropped. Every record of every logger, as the formatter writes it."""
    typed = _fact("FACTSECRET typed by the user", "preference", source="user")
    stored = _fact("FACTSECRET stored earlier", "condition")
    pg = _Pg(
        [_msg(41, "assistant", "MSGSECRET previous answer", _CURRENT, marker=40),
         _msg(7, "user", "MSGSECRET what the user said", _OTHER, marker=3),
         _msg(8, "assistant", "MSGSECRET what the assistant said", _OTHER, marker=3)],
        facts=[typed, stored])
    ops = [
        {"op": "ADD", "type": "area", "topic": "health", "fact": "FACTSECRET new fact"},
        {"op": "UPDATE", "id": "f2", "type": "area", "topic": "health", "fact": "FACTSECRET changed"},
        {"op": "DELETE", "id": "f1"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "FACTSECRET call 0912 345 678"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "FACTSECRET " + "x" * 300},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "FACTSECRET second"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "FACTSECRET third"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "FACTSECRET fourth"},
        {"op": "ADD", "type": "profile", "topic": "health", "fact": "FACTSECRET over the limit"},
    ]
    with caplog.at_level(logging.DEBUG):
        summary, _ = await _run(pg, _llm(ops))

    assert summary["conversations"] == 2 and summary["added"] == 4
    assert len(_done(caplog.records)) == 2
    text = _formatted(caplog.records)
    assert not any(secret in text for secret in ("MSGSECRET", "FACTSECRET", "RAWSECRET"))
    for record in caplog.records:
        assert not any(secret in repr(vars(record))
                       for secret in ("MSGSECRET", "FACTSECRET", "RAWSECRET"))


@pytest.mark.asyncio
async def test_the_done_line_carries_counts_and_ids_only(caplog):
    pg = _Pg([_msg(2, "user", "hello", marker=1)])
    with caplog.at_level(logging.INFO, logger="langgraph.fact_extractor"):
        await _run(pg, _llm([{"op": "ADD", "type": "preference", "topic": "yoga", "fact": "Likes yoga"}]))
    (done,) = _done(caplog.records)
    # A handler that formats the record adds these two.
    standard = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}
    assert set(vars(done)) - standard == {
        "session_id", "message_count", "char_count", "llm_called",
        "added", "updated", "deleted", "repeated", "dropped",
        "input_tokens", "output_tokens", "elapsed_ms"}


# ── The probe ─────────────────────────────────────────────────────────────────


def test_the_probe_measures_this_modules_prompt_and_checks():
    from langgraph_agents.local_tests import run_memory_probe as probe

    assert probe.MEMORY_PROMPT is fe.MEMORY_PROMPT
    assert probe.enforce is fe.enforce
    assert probe.render_user_message is fe.render_user_message
    assert probe.extraction_llm is fe.extraction_llm
    assert probe.request_operations is fe.request_operations
    # No second copy left behind to drift from the one that runs.
    for name in ("_enforce", "_render_user_message", "_Ops", "_looks_like_identifier",
                 "MAX_OPS", "MAX_FACT_CHARS", "CATEGORIES"):
        assert not hasattr(probe, name), name


def test_the_probes_pure_helpers_still_work():
    from langgraph_agents.local_tests import run_memory_probe as probe

    stored = [{"id": "f1", "fact": "Has lower back pain", "type": "profile",
               "topic": "health", "locked": False}]
    kept, dropped = probe.enforce(
        [{"op": "ADD", "type": "profile", "topic": "health", "fact": "Had ACL surgery in March 2025"},
         {"op": "DELETE", "id": "f1"},
         {"op": "ADD", "type": "profile", "topic": "health", "fact": "Call 0912 345 678"}], stored)
    assert [d["reason"] for d in dropped] == ["identifier"]

    facts = probe._apply_ops(stored, kept, [0])
    assert facts == [{"id": "n1", "fact": "Had ACL surgery in March 2025",
                      "type": "profile", "topic": "health",
                      "locked": False}]

    score = probe._score(
        {"ops": [{"op": "ADD", "category": "condition", "groups": [["ACL"], ["2025"]]}],
         "acceptable": [{"op": "DELETE", "id": "f1"}], "forbidden": ["0912"]},
        kept, dropped)
    assert score["verdict"] == "OK" and len(score["found"]) == 1
    assert score["forbidden_in_dropped"] == [{"phrase": "0912", "reason": "identifier"}]
    assert probe._pairs([1, 2, 3, 4]) == [[1, 2], [3, 4]]
    assert probe._pct([3.0, 1.0, 2.0], 0.5) == 2.0


@pytest.mark.asyncio
async def test_the_probes_call_goes_through_this_modules_request():
    from langgraph_agents.local_tests import run_memory_probe as probe

    llm = _llm([{"op": "ADD", "type": "preference", "topic": "yoga", "fact": "Likes yoga"}])
    record = await probe._call(llm, "the user message", [])
    assert llm.ainvoke.await_args.args == (
        [("system", fe.MEMORY_PROMPT), ("user", "the user message")],)
    assert record["parse_ok"] is True and record["error"] is None
    assert record["kept_ops"] == [{"op": "ADD", "type": "preference", "topic": "yoga", "fact": "Likes yoga"}]


def test_the_prompt_wants_repeat_not_add_and_project_scoped_decisions():
    flat = " ".join(fe.MEMORY_PROMPT.split())
    assert "ONE THING, ONE PLACE" in flat
    assert "it already says it: REPEAT it" in flat
    assert "look for a stored item of ANY type about the same thing" in flat
    assert "material the user asked the assistant to work on" in flat
    # v3.2
    assert "when it changes what the assistant should do for the user" in flat
    assert "is unsure they will do is not" in flat
    assert "keep the condition in the fact" in flat
    assert "not one of the app's own settings, even when the user mentions it" in flat
    assert "which model the app runs on" in flat
    assert "makes an exception to it or changes it: UPDATE it" in flat


def test_the_prompt_has_no_safety_flag_and_is_domain_neutral():
    """v3.1 (Tri 23/10): no safety flag in the contract, and no wellness or
    physical-therapy role in the prompt."""
    flat = " ".join(fe.MEMORY_PROMPT.split())
    assert "safety" not in flat.lower()
    assert "physical-therapy" not in flat and "wellness" not in flat
    assert "instruction: how the assistant should behave." in flat
    assert "At most 8 operations" in flat


def test_the_probes_live_slices_follow_what_production_sends():
    from langgraph_agents.local_tests import run_memory_probe as probe

    u1, a1 = {"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}
    u2, a2 = {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}
    # The first user message alone, read from the start; then the previous answer plus
    # the next user message; the closing answer has no user message after it: no slice.
    assert probe._live_slices([u1, a1, u2, a2]) == [
        ([u1], fe._SCOPE_FROM_START),
        ([a1, u2], fe._SCOPE_CONTINUED),
    ]
    assert probe._live_slices([u1, a1]) == [([u1], fe._SCOPE_FROM_START)]
