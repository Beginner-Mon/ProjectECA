"""Short-term memory (memory-v2, A4): what the model is shown about this session.

Facts and summaries travel in their own state fields and reach the planner and
the synthesizer; raw history is every message after the last active summary;
the summariser measures message size and compacts on a turn boundary.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from langgraph_agents.nodes import memory as mem
from langgraph_agents.nodes import planner as planner_mod
from langgraph_agents.nodes import summarizer as summ
from langgraph_agents.nodes import synthesizer as syn
from langgraph_agents.shared import context as ctx_mod

pytestmark = pytest.mark.unit

_FACTS_HEADING = "## What this user has told you about themselves"
_SUMMARY_HEADING = "## Earlier in this conversation"
_PLANNER_FACTS_LINE = "Known about the user (their own account):"

_BUDGETS = {
    "history": 1000,
    "history_compact_at": 2000,
    "user_facts": 300,
    "session_summary": 500,
}


@pytest.fixture
def budgets(monkeypatch):
    """Fixed budgets, so these tests do not move when the YAML is tuned."""
    values = dict(_BUDGETS)
    monkeypatch.setattr(ctx_mod, "_load_context_budgets", lambda: values)
    return values


def _config(session_id="s-1"):
    return RunnableConfig(configurable={
        "user_id": "u-1", "session_id": session_id, "query": "hello",
        "persona_id": "anne", "request_id": "r-1",
    })


# ── A stand-in for the three tables the memory path reads and writes ─────

class FakeDB:
    """Answers by the shape of the statement, as Postgres would for these.

    The code under test still decides every range and every cut; this only
    stores rows and applies the WHERE clause the statement asks for.
    """

    def __init__(self):
        self.messages: list[dict] = []
        self.summaries: list[dict] = []
        self.facts: list[dict] = []
        self.statements: list[str] = []
        self._seq = 100

    def add_message(self, role: str, content: str, token_count=None) -> int:
        # seq_id is one sequence for every session, so a session's ids have gaps.
        self._seq += 3
        self.messages.append({"seq_id": self._seq, "role": role,
                              "content": content, "token_count": token_count})
        return self._seq

    def add_turn(self, question: str, answer: str) -> None:
        self.add_message("user", question)
        # What write_session_turn stores: the whole turn's LLM usage.
        self.add_message("assistant", answer, token_count=6000)

    def add_summary(self, covers_from: int, covers_up_to: int, text: str,
                    status: str = "active") -> None:
        self.summaries.append({
            "covers_from_seq": covers_from, "covers_up_to_seq": covers_up_to,
            "summary_text": text, "status": status,
        })

    def _active(self) -> list[dict]:
        return [s for s in self.summaries if s["status"] == "active"]

    def covered(self) -> int:
        return max((s["covers_up_to_seq"] for s in self._active()), default=0)

    async def connect(self):
        return None

    async def fetchval(self, sql, *args):
        sql = " ".join(sql.split())
        self.statements.append(sql)
        assert "MAX(covers_up_to_seq)" in sql and "status = 'active'" in sql
        return self.covered()

    async def fetch(self, sql, *args):
        sql = " ".join(sql.split())
        self.statements.append(sql)
        if "FROM user_memory" in sql:
            return [{"fact_text": f["fact_text"]} for f in self.facts]
        if "FROM messages" in sql:
            if "FROM summaries" in sql:
                # Coverage resolved inside the statement itself.
                assert "status = 'active'" in sql
                low, high = self.covered(), None
            else:
                low = args[1]
                high = args[2] if len(args) > 2 else None
            return [m for m in self.messages
                    if m["seq_id"] > low and (high is None or m["seq_id"] <= high)]
        if "FROM summaries" in sql:
            assert "status = 'active'" in sql
            rows = sorted(self._active(), key=lambda s: s["covers_from_seq"])
            return [{"summary_text": s["summary_text"]} for s in rows]
        raise AssertionError(f"unexpected statement: {sql}")

    async def execute(self, sql, *args):
        sql = " ".join(sql.split())
        self.statements.append(sql)
        assert "INSERT INTO summaries" in sql
        assert "ON CONFLICT ON CONSTRAINT uq_chunk DO NOTHING" in sql
        _session, text, covers_from, covers_up_to, _embedding = args
        if any(s["covers_from_seq"] == covers_from for s in self.summaries):
            return
        self.add_summary(covers_from, covers_up_to, text)


# ── Facts → capped text (shared by synthesizer and planner) ──────────────

def test_render_user_facts_lists_every_fact_that_fits(budgets):
    text = ctx_mod.render_user_facts(["Left knee surgery in July 2026",
                                      "Prefers morning sessions"])
    assert text == "- Left knee surgery in July 2026\n- Prefers morning sessions"


def test_render_user_facts_is_empty_without_facts(budgets):
    assert ctx_mod.render_user_facts([]) == ""


def test_render_user_facts_drops_whole_facts_from_the_end(budgets):
    """Priority order in, so the tail goes first — and no fact is cut in half."""
    budgets["user_facts"] = 20          # 80 characters
    facts = [
        "No overhead lifting after shoulder surgery",   # restriction
        "Type 2 diabetes since 2019",                    # condition
        "Likes swimming on weekends",
        "Works night shifts",
    ]
    text = ctx_mod.render_user_facts(facts)
    assert text == ("- No overhead lifting after shoulder surgery\n"
                    "- Type 2 diabetes since 2019")
    assert len(text) <= ctx_mod.budget_chars("user_facts")
    for line in text.split("\n"):
        assert line[2:] in facts


def test_render_user_facts_never_returns_a_partial_fact(budgets):
    budgets["user_facts"] = 5           # 20 characters: the first fact cannot fit
    assert ctx_mod.render_user_facts(["No overhead lifting after shoulder surgery",
                                      "Vegan"]) == ""


# ── Synthesizer: both blocks reach the system prompt ─────────────────────

class _FakeAI:
    content = "ok"
    usage_metadata = None
    response_metadata = {}


def _capturing_llm(captured: dict) -> MagicMock:
    async def fake_ainvoke(msgs):
        captured["msgs"] = msgs
        return _FakeAI()

    llm = MagicMock()
    llm.ainvoke = fake_ainvoke
    return llm


async def _synth_messages(**state_fields) -> list:
    state = {
        "messages": [HumanMessage(content="my knee hurts"),
                     AIMessage(content="Try heel slides.")],
        "resolved_query": "what about stairs",
        "required_outputs": [],
        "needs_clarification": False,
        "total_tokens": 0,
        **state_fields,
    }
    config = {"configurable": {
        "request_id": "r", "persona_id": "anne", "query": "what about stairs",
        "locale": "en",
    }}
    captured: dict = {}
    with patch.object(syn, "get_chat_model", return_value=_capturing_llm(captured)):
        await syn.synthesizer_node(state, config)
    assert captured.get("msgs"), "synthesizer never called the LLM"
    return captured["msgs"]


@pytest.mark.asyncio
async def test_synthesizer_system_prompt_carries_user_facts(budgets):
    msgs = await _synth_messages(user_facts=["Left knee surgery in July 2026"])
    system = msgs[0].content
    assert isinstance(msgs[0], SystemMessage)
    assert _FACTS_HEADING in system
    assert "- Left knee surgery in July 2026" in system
    block = system[system.index(_FACTS_HEADING):]
    assert "earlier conversations" in block
    assert "not a diagnosis" in block
    assert "only when it changes what you should say or do" in block
    assert "go by today's message" in block


@pytest.mark.asyncio
async def test_synthesizer_system_prompt_carries_session_summary(budgets):
    msgs = await _synth_messages(session_summary=[
        "The user reported left knee pain on stairs.",
        "Heel slides were given, 2 sets of 10; the user agreed to try them.",
    ])
    system = msgs[0].content
    assert _SUMMARY_HEADING in system
    first = system.index("left knee pain on stairs")
    second = system.index("Heel slides were given")
    assert system.index(_SUMMARY_HEADING) < first < second     # oldest first


@pytest.mark.asyncio
async def test_synthesizer_prompt_is_unchanged_without_facts_or_summary(budgets):
    bare = await _synth_messages()
    empty = await _synth_messages(user_facts=[], session_summary=[])
    assert [m.content for m in bare] == [m.content for m in empty]
    assert _FACTS_HEADING not in bare[0].content
    assert _SUMMARY_HEADING not in bare[0].content


@pytest.mark.asyncio
async def test_synthesizer_keeps_the_raw_history_beside_the_new_blocks(budgets):
    msgs = await _synth_messages(user_facts=["Vegan"],
                                 session_summary=["Earlier: knee pain."])
    contents = [m.content for m in msgs]
    assert "my knee hurts" in contents
    assert "Try heel slides." in contents


def test_user_facts_block_follows_the_about_you_pattern(budgets):
    assert syn._build_user_facts([]) == ""
    block = syn._build_user_facts(["Vegan"])
    assert block.startswith(f"\n\n{_FACTS_HEADING}\n")
    assert block.endswith("- Vegan")


def test_user_facts_block_keeps_restriction_and_condition_whole(budgets):
    budgets["user_facts"] = 20
    block = syn._build_user_facts([
        "No overhead lifting after shoulder surgery",
        "Type 2 diabetes since 2019",
        "Likes swimming on weekends",
    ])
    assert "- No overhead lifting after shoulder surgery" in block
    assert "- Type 2 diabetes since 2019" in block
    assert "swimming" not in block
    assert "Likes" not in block


def test_session_summary_block_follows_the_about_you_pattern(budgets):
    assert syn._build_session_summary([]) == ""
    block = syn._build_session_summary(["Knee pain reported."])
    assert block.startswith(f"\n\n{_SUMMARY_HEADING}\n")
    assert block.endswith("Knee pain reported.")


def test_session_summary_block_drops_the_oldest_chunks_over_budget(budgets):
    budgets["session_summary"] = 30     # 120 characters
    chunks = ["oldest " + "a" * 60, "middle " + "b" * 60, "newest " + "c" * 40]
    block = syn._build_session_summary(chunks)
    assert "oldest" not in block
    assert block.index("middle") < block.index("newest")


@pytest.mark.asyncio
async def test_prompt_blocks_log_reports_both_new_blocks(budgets, caplog):
    with caplog.at_level(logging.INFO, logger="langgraph.synthesizer"):
        await _synth_messages(user_facts=["Vegan"],
                              session_summary=["Knee pain reported."])
    done = [r for r in caplog.records if r.getMessage() == "node_complete"]
    assert len(done) == 1
    assert done[0].prompt_blocks["user_facts"] == len(syn._build_user_facts(["Vegan"]))
    assert done[0].prompt_blocks["session_summary"] == len(
        syn._build_session_summary(["Knee pain reported."]))


@pytest.mark.asyncio
async def test_prompt_blocks_log_reports_both_new_blocks_on_the_addition_pass(
        budgets, caplog):
    with caplog.at_level(logging.INFO, logger="langgraph.synthesizer"):
        await _synth_messages(
            user_facts=["Vegan"], session_summary=["Knee pain reported."],
            retry_count=1, grader_feedback="- how often",
            final_answer="Do heel slides.")
    done = [r for r in caplog.records if r.getMessage() == "node_complete"]
    assert len(done) == 1 and done[0].mode == "addition"
    assert done[0].prompt_blocks["user_facts"] > 0
    assert done[0].prompt_blocks["session_summary"] > 0


# ── Planner: facts in the user message, hint from user/assistant turns ───

async def _planner_prompt(state: dict, query: str = "what about it") -> list:
    plan = planner_mod.PlanOutput(resolved_query=query)
    with patch.object(planner_mod, "get_chat_model") as get_model:
        ainvoke = AsyncMock(return_value=plan)
        get_model.return_value.with_structured_output.return_value.ainvoke = ainvoke
        config = RunnableConfig(configurable={
            "user_id": "u", "session_id": "s", "query": query,
            "persona_id": "anne", "request_id": "r",
        })
        await planner_mod.planner_node(state, config)
    return ainvoke.await_args.args[0]


_HISTORY = [HumanMessage(content="my knee hurts"),
            AIMessage(content="Try heel slides.")]
_HINT = "\n\nRecent context:\n[human]: my knee hurts\n[ai]: Try heel slides."


@pytest.mark.asyncio
async def test_planner_user_message_carries_user_facts(budgets):
    prompt = await _planner_prompt({
        "messages": list(_HISTORY),
        "user_facts": ["Left knee surgery in July 2026", "Vegan"],
    })
    assert prompt[0] == ("system", planner_mod._PLANNER_SYSTEM_PROMPT)
    role, user_msg = prompt[1]
    assert role == "user"
    assert user_msg == (
        "what about it" + _HINT
        + f"\n\n{_PLANNER_FACTS_LINE}\n- Left knee surgery in July 2026\n- Vegan")


@pytest.mark.asyncio
async def test_planner_input_is_byte_for_byte_the_same_without_facts(budgets):
    for state in ({"messages": list(_HISTORY)},
                  {"messages": list(_HISTORY), "user_facts": []}):
        prompt = await _planner_prompt(state)
        assert prompt == [("system", planner_mod._PLANNER_SYSTEM_PROMPT),
                          ("user", "what about it" + _HINT)]
    assert await _planner_prompt({"messages": []}, query="hello") == [
        ("system", planner_mod._PLANNER_SYSTEM_PROMPT), ("user", "hello")]


@pytest.mark.asyncio
async def test_planner_facts_use_the_same_whole_fact_cap(budgets):
    budgets["user_facts"] = 20
    prompt = await _planner_prompt({"messages": [], "user_facts": [
        "No overhead lifting after shoulder surgery",
        "Type 2 diabetes since 2019",
        "Likes swimming on weekends",
    ]})
    assert prompt[1][1] == (
        f"what about it\n\n{_PLANNER_FACTS_LINE}\n"
        "- No overhead lifting after shoulder surgery\n"
        "- Type 2 diabetes since 2019")


@pytest.mark.asyncio
async def test_planner_context_hint_takes_only_user_and_assistant_turns(budgets):
    prompt = await _planner_prompt({"messages": [
        SystemMessage(content="[USER FACTS]\n- something internal"),
        *_HISTORY,
        ToolMessage(content="[]", name="kb_search", tool_call_id="t1"),
    ]})
    assert prompt[1][1] == "what about it" + _HINT


def test_planner_system_prompt_has_no_rule_about_user_facts():
    assert "Known about the user" not in planner_mod._PLANNER_SYSTEM_PROMPT
    assert "user_facts" not in planner_mod._PLANNER_SYSTEM_PROMPT


# ── Memory node ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_node_returns_facts_summary_and_only_uncovered_history(budgets):
    db = FakeDB()
    db.facts = [{"fact_text": "Left knee surgery in July 2026"}]
    first = db.add_message("user", "old question")
    last_covered = db.add_message("assistant", "old answer")
    db.add_summary(first, last_covered, "The user asked about knee pain.")
    db.add_turn("new question", "new answer")
    db.add_turn("newer question", "newer answer")

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config())

    assert out["user_facts"] == ["Left knee surgery in July 2026"]
    assert out["session_summary"] == ["The user asked about knee pain."]
    assert not any(isinstance(m, SystemMessage) for m in out["messages"])
    assert [(type(m), m.content) for m in out["messages"]] == [
        (HumanMessage, "new question"), (AIMessage, "new answer"),
        (HumanMessage, "newer question"), (AIMessage, "newer answer"),
    ]
    # Three independent loads, one statement each.
    assert len(db.statements) == 3


@pytest.mark.asyncio
async def test_memory_node_returns_empty_fields_for_a_new_session(budgets):
    with patch.object(mem, "get_pg_client", return_value=FakeDB()):
        out = await mem.memory_node({"messages": []}, _config())
    assert out == {"messages": [], "user_facts": [], "session_summary": []}


@pytest.mark.asyncio
async def test_a_dirty_summary_does_not_count_as_coverage(budgets):
    db = FakeDB()
    first = db.add_message("user", "question")
    last = db.add_message("assistant", "answer")
    db.add_summary(first, last, "stale", status="dirty")

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config())

    assert [m.content for m in out["messages"]] == ["question", "answer"]
    assert out["session_summary"] == []


@pytest.mark.asyncio
async def test_raw_history_is_one_statement_that_resolves_coverage_itself(budgets):
    db = FakeDB()
    db.add_turn("q", "a")
    with patch.object(mem, "get_pg_client", return_value=db):
        rows = await mem._load_raw_history("s-1")
    assert [r["content"] for r in rows] == ["q", "a"]
    assert len(db.statements) == 1
    sql = db.statements[0]
    assert "MAX(covers_up_to_seq)" in sql and "status = 'active'" in sql
    assert "ORDER BY seq_id" in sql
    assert "LIMIT" not in sql and "token_count" not in sql


@pytest.mark.asyncio
async def test_raw_history_load_failure_degrades_to_empty(budgets):
    pg = AsyncMock()
    pg.fetch.side_effect = RuntimeError("DB down")
    with patch.object(mem, "get_pg_client", return_value=pg):
        assert await mem._load_raw_history("s-1") == []


def _thousand_token_turn(db: FakeDB, i: int) -> None:
    db.add_turn(f"q{i} ".ljust(400, "x"), f"a{i} ".ljust(3600, "y"))   # 100 + 900


@pytest.mark.asyncio
async def test_history_over_the_ceiling_drops_the_oldest_and_says_so(budgets, caplog):
    """Ceiling = history_compact_at + history = 3000 tokens."""
    db = FakeDB()
    for i in range(1, 5):
        _thousand_token_turn(db, i)

    with patch.object(mem, "get_pg_client", return_value=db), \
         caplog.at_level(logging.WARNING, logger="langgraph.memory"):
        out = await mem.memory_node({"messages": []}, _config())

    kept = [m.content[:2] for m in out["messages"]]
    assert kept == ["q2", "a2", "q3", "a3", "q4", "a4"]
    records = [r for r in caplog.records if r.getMessage() == "history_overflow_dropped"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].dropped_messages == 2
    assert records[0].kept_messages == 6
    assert records[0].dropped_tokens == 1000
    assert records[0].kept_tokens == 3000
    assert records[0].session_id == "s-1"


@pytest.mark.asyncio
async def test_history_at_the_ceiling_is_left_alone(budgets, caplog):
    db = FakeDB()
    for i in range(1, 4):
        _thousand_token_turn(db, i)                 # 3000 tokens in all

    with patch.object(mem, "get_pg_client", return_value=db), \
         caplog.at_level(logging.WARNING, logger="langgraph.memory"):
        out = await mem.memory_node({"messages": []}, _config())

    assert len(out["messages"]) == 6
    assert "history_overflow_dropped" not in caplog.text


@pytest.mark.asyncio
async def test_user_facts_are_asked_for_in_safety_first_order(budgets):
    pg = AsyncMock()
    pg.fetch.return_value = [{"fact_text": "No overhead lifting"},
                             {"fact_text": "Type 2 diabetes"},
                             {"fact_text": "Vegan"}]
    with patch.object(mem, "get_pg_client", return_value=pg):
        facts = await mem._load_user_facts("u-1")

    assert facts == ["No overhead lifting", "Type 2 diabetes", "Vegan"]
    sql = " ".join(pg.fetch.await_args.args[0].split())
    order = sql[sql.index("ORDER BY"):]
    assert order.index("'restriction'") < order.index("'condition'") < order.index("created_at DESC")
    assert order.endswith("LIMIT 50")
    assert "valid = true" in sql


def test_memory_node_no_longer_knows_the_short_term_cache():
    assert not hasattr(mem, "get_stm")


# ── No hole: every message is raw or summarised, never neither, never both ──

# Characters per (question, answer): short asks, full clinical answers.
_TURNS = [(60, 900), (120, 1800), (45, 2400), (200, 2100), (80, 1500), (30, 300),
          (150, 2600), (90, 1900), (60, 2200), (110, 1700), (40, 800), (170, 2500)]


async def _assert_no_hole(db: FakeDB) -> dict:
    out = await mem.memory_node({"messages": []}, _config())
    raw = {m.content for m in out["messages"]}
    active = db._active()
    for m in db.messages:
        in_raw = m["content"] in raw
        in_summary = sum(s["covers_from_seq"] <= m["seq_id"] <= s["covers_up_to_seq"]
                         for s in active)
        assert in_raw + in_summary == 1, (
            f"seq {m['seq_id']}: raw={in_raw}, summaries={in_summary}")
    if out["messages"]:
        assert isinstance(out["messages"][0], HumanMessage)
    assert len(out["session_summary"]) == len(active)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("summarise_first", [True, False])
async def test_twelve_turn_session_has_no_hole(budgets, caplog, summarise_first):
    """housekeep starts with the turn, so compaction may land before or after
    the turn's own rows are written. Either way nothing falls between."""
    db = FakeDB()
    embed = MagicMock()
    embed.aembed_passage = AsyncMock(return_value=[0.1] * 384)

    async def fake_summary(rows, session_id):
        return f"summary of seq {rows[0]['seq_id']}..{rows[-1]['seq_id']}"

    ceiling = budgets["history_compact_at"] + budgets["history"]
    with patch.object(mem, "get_pg_client", return_value=db), \
         patch.object(summ, "get_pg_client", return_value=db), \
         patch.object(summ, "get_embedding_service", return_value=embed), \
         patch.object(summ, "_summarize_messages", side_effect=fake_summary), \
         caplog.at_level(logging.WARNING):
        for turn, (q_chars, a_chars) in enumerate(_TURNS, start=1):
            seen = await _assert_no_hole(db)        # what this turn's model is shown
            assert sum(len(m.content) // 4 for m in seen["messages"]) <= ceiling
            if summarise_first:
                await summ.maybe_summarize("s-1")
                await _assert_no_hole(db)
            db.add_turn(f"q{turn}: " + "x" * q_chars, f"a{turn}: " + "y" * a_chars)
            if not summarise_first:
                await summ.maybe_summarize("s-1")
            await _assert_no_hole(db)

    assert len(db.summaries) >= 2, "the session never compacted"
    ranges = sorted((s["covers_from_seq"], s["covers_up_to_seq"]) for s in db.summaries)
    for (_, up_to), (next_from, _) in zip(ranges, ranges[1:]):
        assert next_from == up_to + 1
    assert "history_overflow_dropped" not in caplog.text
    assert "summarizer_" not in caplog.text


# ── Summariser: when to compact and where to cut ─────────────────────────

def _rows(*turns, start_seq=200):
    """turns = (question_tokens, answer_tokens); answer None = question only."""
    rows, seq = [], start_seq
    for q_tokens, a_tokens in turns:
        seq += 2
        rows.append({"seq_id": seq, "role": "user", "content": "q" * (q_tokens * 4),
                     "token_count": None})
        if a_tokens is not None:
            seq += 2
            rows.append({"seq_id": seq, "role": "assistant",
                         "content": "a" * (a_tokens * 4), "token_count": 6000})
    return rows


async def _maybe_summarize(rows, covered=0):
    pg = AsyncMock()
    pg.fetchval.return_value = covered
    pg.fetch.return_value = rows
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_run_summarize", new=AsyncMock()) as run:
        await summ.maybe_summarize("s-1")
    return run


@pytest.mark.asyncio
async def test_huge_token_count_with_short_content_does_not_trigger(budgets):
    rows = [{"seq_id": i, "role": "assistant", "content": "ok", "token_count": 50_000}
            for i in range(1, 9)]
    run = await _maybe_summarize(rows)
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_content_triggers_whatever_token_count_says(budgets):
    rows = _rows((100, 900), (100, 900), (100, 900))
    for r in rows:
        r["token_count"] = 1
    run = await _maybe_summarize(rows)
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_at_the_threshold_nothing_is_summarised(budgets):
    run = await _maybe_summarize(_rows((500, 500), (500, 500)))       # 2000
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_above_the_threshold_the_oldest_part_is_summarised(budgets):
    rows = _rows((500, 500), (500, 501))                               # 2001
    run = await _maybe_summarize(rows, covered=41)
    run.assert_awaited_once_with("s-1", 41, rows[1]["seq_id"])


@pytest.mark.asyncio
async def test_newest_turns_stay_raw_while_they_fit_the_history_budget(budgets):
    rows = _rows((100, 800), (50, 250), (50, 250), (50, 250), (50, 250))   # 2100
    run = await _maybe_summarize(rows)
    # Three newest turns = 900 tokens; a fourth would make 1200.
    run.assert_awaited_once_with("s-1", 0, rows[3]["seq_id"])
    assert rows[4]["role"] == "user"


@pytest.mark.asyncio
async def test_the_cut_never_splits_a_turn(budgets):
    rows = _rows((100, 500), (100, 500), (100, 500), (100, 500))           # 2400
    run = await _maybe_summarize(rows)
    # Only the latest turn fits (600); the one before would make 1200.
    run.assert_awaited_once_with("s-1", 0, rows[5]["seq_id"])
    assert rows[5]["role"] == "assistant" and rows[6]["role"] == "user"


@pytest.mark.asyncio
async def test_the_latest_turn_stays_raw_even_when_it_alone_is_over_budget(budgets):
    rows = _rows((100, 400), (100, 2400))
    run = await _maybe_summarize(rows)
    run.assert_awaited_once_with("s-1", 0, rows[1]["seq_id"])

    run = await _maybe_summarize(_rows((100, 2400)))
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_question_still_waiting_for_its_answer_counts_as_the_latest_turn(budgets):
    rows = _rows((100, 700), (100, 700), (100, 500), (20, None))           # 2220
    run = await _maybe_summarize(rows)
    # Raw: the open question (20) + the turn before it (600); a third makes 1420.
    run.assert_awaited_once_with("s-1", 0, rows[3]["seq_id"])
    assert rows[4]["role"] == "user"


@pytest.mark.asyncio
async def test_run_summarize_stops_at_the_given_seq(budgets):
    pg = AsyncMock()
    pg.fetch.return_value = _rows((10, 10))
    embed = MagicMock()
    embed.aembed_passage = AsyncMock(return_value=[0.1] * 384)
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "get_embedding_service", return_value=embed), \
         patch.object(summ, "_summarize_messages", new=AsyncMock(return_value="S")):
        await summ._run_summarize("s-1", 200, 204)

    sql, *args = pg.fetch.await_args.args
    assert args == ["s-1", 200, 204]
    assert "seq_id <= $3" in " ".join(sql.split())
    pg.execute.assert_awaited_once()
    insert_sql, *insert_args = pg.execute.await_args.args
    assert "ON CONFLICT ON CONSTRAINT uq_chunk DO NOTHING" in insert_sql
    assert insert_args[:4] == ["s-1", "S", 201, 204]


@pytest.mark.asyncio
async def test_run_summarize_without_a_bound_takes_the_whole_remainder(budgets):
    pg = AsyncMock()
    rows = _rows((10, 10), (10, 10))
    pg.fetch.return_value = rows
    embed = MagicMock()
    embed.aembed_passage = AsyncMock(return_value=[0.1] * 384)
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "get_embedding_service", return_value=embed), \
         patch.object(summ, "_summarize_messages", new=AsyncMock(return_value="S")):
        await summ._run_summarize("s-1", 200)

    assert pg.fetch.await_args.args[1:] == ("s-1", 200, None)
    assert pg.execute.await_args.args[1:5] == ("s-1", "S", 201, rows[-1]["seq_id"])


@pytest.mark.asyncio
async def test_summaries_are_written_with_the_memory_role():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=" A summary. "))
    with patch.object(summ, "get_chat_model", return_value=llm) as get_model:
        text = await summ._summarize_messages(
            [{"role": "user", "content": "my knee hurts"},
             {"role": "assistant", "content": "try heel slides"}], "s-1")

    get_model.assert_called_once_with("memory")
    assert text == "A summary."
    prompt = llm.ainvoke.await_args.args[0]
    assert "User: my knee hurts\nAssistant: try heel slides" in prompt
    assert "same language" in prompt.lower()
    assert "120 words" in prompt
