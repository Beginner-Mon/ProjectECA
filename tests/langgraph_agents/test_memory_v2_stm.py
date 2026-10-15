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


_QUESTION_ID = "33333333-3333-3333-3333-333333333333"


def _config(session_id="s-1", user_message_id=None):
    configurable = {
        "user_id": "u-1", "session_id": session_id, "query": "hello",
        "persona_id": "anne", "request_id": "r-1",
    }
    if user_message_id is not None:
        # /chat names the running turn's question so the node can leave it out.
        configurable["user_message_id"] = user_message_id
    return RunnableConfig(configurable=configurable)


# ── A stand-in for the tables the memory path reads and writes ─────────

class FakeDB:
    """Answers by the shape of the statement, as Postgres would for these.

    The code under test still decides every range and every cut; this only
    stores rows and applies the WHERE clause the statement asks for. One
    rolling summary per session (stm_summary/stm_up_to_seq), not chunks.
    """

    def __init__(self):
        self.messages: list[dict] = []
        self.facts: list[dict] = []
        self.statements: list[str] = []
        self._seq = 100
        self.stm_summary: str | None = None
        self.stm_up_to_seq: int = 0

    def add_message(self, role: str, content: str, token_count=None,
                    message_id=None) -> int:
        # seq_id is one sequence for every session, so a session's ids have gaps.
        self._seq += 3
        self.messages.append({"id": message_id or f"m-{self._seq}",
                              "seq_id": self._seq, "role": role,
                              "content": content, "token_count": token_count})
        return self._seq

    def add_turn(self, question: str, answer: str) -> None:
        self.add_message("user", question)
        # What the answer row stores: the whole turn's LLM usage.
        self.add_message("assistant", answer, token_count=6000)

    def set_stm(self, text: str, up_to_seq: int) -> None:
        self.stm_summary = text
        self.stm_up_to_seq = up_to_seq

    async def connect(self):
        return None

    async def fetchrow(self, sql, *args):
        # compact_stm reads the rolling-summary head.
        self.statements.append(" ".join(sql.split()))
        return {"stm_summary": self.stm_summary,
                "stm_up_to_seq": self.stm_up_to_seq}

    async def fetch(self, sql, *args):
        sql = " ".join(sql.split())
        self.statements.append(sql)
        if "FROM user_memory" in sql:
            return [{"fact_text": f["fact_text"]} for f in self.facts]
        if "FROM conversations c" in sql:
            # The memory node's single STM statement: the summary rides along
            # every message row; with no message after the summary the join
            # still yields one row of NULLs.
            _session, exclude_id, limit = args
            assert "stm_up_to_seq" in sql and "LIMIT $3" in sql
            rows = [m for m in self.messages
                    if m["seq_id"] > self.stm_up_to_seq and m["id"] != exclude_id]
            rows = rows[::-1][:limit]
            if not rows:
                return [{"stm_summary": self.stm_summary,
                         "role": None, "content": None}]
            return [{"stm_summary": self.stm_summary,
                     "role": m["role"], "content": m["content"]} for m in rows]
        if "ORDER BY seq_id DESC" in sql:
            # _load_latest_turns: newest first, bounded, without the question.
            _session, exclude_id, limit = args
            rows = [m for m in self.messages if m["id"] != exclude_id]
            return rows[::-1][:limit]
        if "FROM messages" in sql:
            # compact_stm: messages after the summary, oldest first.
            return [m for m in self.messages if m["seq_id"] > args[1]]
        raise AssertionError(f"unexpected statement: {sql}")

    async def execute(self, sql, *args):
        sql = " ".join(sql.split())
        self.statements.append(sql)
        # compact_stm compare-and-set: only the first writer wins.
        assert "stm_up_to_seq = $4" in sql
        _session, text, up_to, frm = args
        if self.stm_up_to_seq != frm:
            return "UPDATE 0"
        self.stm_summary, self.stm_up_to_seq = text, up_to
        return "UPDATE 1"


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
    db.add_turn("old question", "old answer")
    db.set_stm("The user asked about knee pain.", db.messages[-1]["seq_id"])
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
    # Two independent loads, one statement each.
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_memory_node_returns_empty_fields_for_a_new_session(budgets):
    with patch.object(mem, "get_pg_client", return_value=FakeDB()):
        out = await mem.memory_node({"messages": []}, _config())
    assert out == {"messages": [], "user_facts": [], "session_summary": []}


@pytest.mark.asyncio
async def test_stm_summary_alone_counts_as_coverage(budgets):
    db = FakeDB()
    db.add_turn("question", "answer")
    db.set_stm("stale", db.messages[-1]["seq_id"])

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config())

    assert out["session_summary"] == ["stale"]


@pytest.mark.asyncio
async def test_stm_read_is_one_statement_that_resolves_coverage_itself(budgets):
    db = FakeDB()
    db.add_turn("q", "a")
    with patch.object(mem, "get_pg_client", return_value=db):
        summary, rows = await mem._load_stm("s-1")
    # Asked for newest first (that is what the row limit keeps), handed on oldest first.
    assert summary == []
    assert [r["content"] for r in rows] == ["q", "a"]
    assert len(db.statements) == 1
    sql = db.statements[0]
    assert "stm_up_to_seq" in sql
    assert "ORDER BY m.seq_id DESC LIMIT $3" in sql
    assert "token_count" not in sql


@pytest.mark.asyncio
async def test_stm_read_is_bounded_to_the_newest_rows(budgets):
    db = FakeDB()
    for i in range(1, 251):
        db.add_message("user" if i % 2 else "assistant", f"m{i}")
    with patch.object(mem, "get_pg_client", return_value=db):
        summary, rows = await mem._load_stm("s-1")
    assert mem._RAW_HISTORY_MAX_ROWS == 200
    assert [r["content"] for r in rows] == [f"m{i}" for i in range(51, 251)]


@pytest.mark.asyncio
async def test_stm_read_failure_degrades_to_empty(budgets):
    pg = AsyncMock()
    pg.fetch.side_effect = RuntimeError("DB down")
    with patch.object(mem, "get_pg_client", return_value=pg):
        assert await mem._load_stm("s-1") == ([], [])


# ── The running turn's own question (written by /chat as the turn starts) ──

@pytest.mark.asyncio
async def test_memory_node_leaves_out_the_question_of_the_running_turn(budgets):
    """The synthesizer adds the current question itself; from the history too,
    the model would read it twice."""
    db = FakeDB()
    db.add_turn("first question", "first answer")
    db.add_message("user", "hello", message_id=_QUESTION_ID)

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config(user_message_id=_QUESTION_ID))

    assert [m.content for m in out["messages"]] == ["first question", "first answer"]


@pytest.mark.asyncio
async def test_memory_node_excludes_nothing_when_no_question_id_is_given(budgets):
    db = FakeDB()
    db.add_turn("first question", "first answer")
    db.add_message("user", "hello", message_id=_QUESTION_ID)

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config())

    assert [m.content for m in out["messages"]] == [
        "first question", "first answer", "hello"]


@pytest.mark.asyncio
async def test_an_earlier_unanswered_question_stays_in_the_history(budgets):
    """A turn that died left its question and no answer. The next turn's model
    is shown that the user asked."""
    db = FakeDB()
    db.add_turn("first question", "first answer")
    db.add_message("user", "the turn that died")
    db.add_message("user", "hello", message_id=_QUESTION_ID)

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config(user_message_id=_QUESTION_ID))

    assert [(type(m), m.content) for m in out["messages"]] == [
        (HumanMessage, "first question"), (AIMessage, "first answer"),
        (HumanMessage, "the turn that died"),
    ]


# ── Back in a conversation that a summary covers from end to end ─────────

def _summarise_everything(db: FakeDB, text: str = "The whole conversation.") -> None:
    db.set_stm(text, db.messages[-1]["seq_id"])


@pytest.mark.asyncio
async def test_a_fully_summarised_conversation_still_gets_its_last_turns_verbatim(budgets):
    """Another turn's housekeeping summarised this conversation after the user
    left it. Back in it, 120 words of summary are not the last exchange."""
    db = FakeDB()
    for i in range(1, 5):
        db.add_turn(f"q{i} ".ljust(400, "x"), f"a{i} ".ljust(1200, "y"))   # 100 + 300
    _summarise_everything(db)
    db.add_message("user", "I'm back", message_id=_QUESTION_ID)

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config(user_message_id=_QUESTION_ID))

    assert out["session_summary"] == ["The whole conversation."]
    # `history` is 1000 tokens: two turns fit, a third would make 1200.
    assert [(type(m), m.content[:2]) for m in out["messages"]] == [
        (HumanMessage, "q3"), (AIMessage, "a3"), (HumanMessage, "q4"), (AIMessage, "a4"),
    ]
    assert len(db.statements) == 3          # the two loads, then this one read
    assert db.statements[-1].endswith("ORDER BY seq_id DESC LIMIT $3")
    assert "FROM summaries" not in db.statements[-1]


@pytest.mark.asyncio
async def test_the_last_turn_comes_back_whole_even_over_the_history_budget(budgets):
    db = FakeDB()
    db.add_turn("q1", "a1")
    db.add_turn("q2 ".ljust(400, "x"), "a2 ".ljust(6000, "y"))             # 100 + 1500
    _summarise_everything(db)

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config())

    assert [m.content[:2] for m in out["messages"]] == ["q2", "a2"]


@pytest.mark.asyncio
async def test_summaries_and_raw_history_do_not_overlap_while_anything_is_uncovered(budgets):
    """One unanswered question after the summary is enough: no second read."""
    db = FakeDB()
    db.add_turn("old question", "old answer")
    _summarise_everything(db)
    db.add_message("user", "the turn that died")
    db.add_message("user", "hello", message_id=_QUESTION_ID)

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config(user_message_id=_QUESTION_ID))

    assert [m.content for m in out["messages"]] == ["the turn that died"]
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_a_new_session_makes_no_second_read(budgets):
    db = FakeDB()
    db.add_message("user", "hello", message_id=_QUESTION_ID)

    with patch.object(mem, "get_pg_client", return_value=db):
        out = await mem.memory_node({"messages": []}, _config(user_message_id=_QUESTION_ID))

    assert out == {"messages": [], "user_facts": [], "session_summary": []}
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_latest_turns_load_failure_degrades_to_empty(budgets):
    pg = AsyncMock()
    pg.fetch.side_effect = RuntimeError("DB down")
    with patch.object(mem, "get_pg_client", return_value=pg):
        assert await mem._load_latest_turns("s-1") == []


# ── Whole turns from the newest end ──────────────────────────────────────

def _history(*messages):
    """messages = (role, tokens); content names the position: u0, a1, u2..."""
    return [{"role": role, "content": f"{role[0]}{i} ".ljust(tokens * 4, "x")}
            for i, (role, tokens) in enumerate(messages)]


def _names(history):
    return [m["content"].split()[0] for m in history]


def test_latest_turns_keeps_the_newest_whole_turns_that_fit():
    history = _history(("user", 100), ("assistant", 400),
                       ("user", 100), ("assistant", 400),
                       ("user", 100), ("assistant", 400))
    assert _names(mem._latest_turns(history, 1000)) == ["u2", "a3", "u4", "a5"]
    assert _names(mem._latest_turns(history, 1500)) == ["u0", "a1", "u2", "a3", "u4", "a5"]


def test_latest_turns_keeps_the_latest_turn_whatever_its_size():
    history = _history(("user", 100), ("assistant", 400),
                       ("user", 100), ("assistant", 5000))
    assert _names(mem._latest_turns(history, 1000)) == ["u2", "a3"]


def test_latest_turns_counts_an_unanswered_question_as_a_turn():
    history = _history(("user", 100), ("assistant", 400),
                       ("user", 300),                      # the turn that died
                       ("user", 100), ("assistant", 400))
    assert _names(mem._latest_turns(history, 800)) == ["u2", "u3", "a4"]
    assert _names(mem._latest_turns(history, 700)) == ["u3", "a4"]


def test_latest_turns_leaves_out_a_leading_piece_with_no_question():
    """The row limit can cut the oldest turn in two. Half a turn is not a turn."""
    history = _history(("assistant", 100), ("user", 100), ("assistant", 100))
    assert _names(mem._latest_turns(history, 1000)) == ["u1", "a2"]


def test_latest_turns_of_nothing_is_nothing():
    assert mem._latest_turns([], 1000) == []


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
async def test_history_over_the_ceiling_is_cut_at_the_start_of_a_turn(budgets, caplog):
    """Dropping the long first question alone would fit the ceiling, and leave
    a history that opens with an answer to nothing."""
    db = FakeDB()
    db.add_turn("q1 ".ljust(4800, "x"), "a1 ".ljust(1600, "y"))            # 1200 + 400
    db.add_turn("q2 ".ljust(400, "x"), "a2 ".ljust(3600, "y"))             # 100 + 900
    db.add_turn("q3 ".ljust(400, "x"), "a3 ".ljust(3600, "y"))             # 100 + 900
    db.add_turn("q4 ".ljust(400, "x"), "a4 ".ljust(2000, "y"))             # 100 + 500

    with patch.object(mem, "get_pg_client", return_value=db), \
         caplog.at_level(logging.WARNING, logger="langgraph.memory"):
        out = await mem.memory_node({"messages": []}, _config())

    assert isinstance(out["messages"][0], HumanMessage)
    assert [m.content[:2] for m in out["messages"]] == ["q2", "a2", "q3", "a3", "q4", "a4"]
    records = [r for r in caplog.records if r.getMessage() == "history_overflow_dropped"]
    assert len(records) == 1
    assert records[0].dropped_messages == 2
    assert records[0].dropped_tokens == 1600
    assert records[0].kept_tokens == 2600


@pytest.mark.asyncio
async def test_history_overflow_never_drops_the_latest_turn(budgets, caplog):
    db = FakeDB()
    db.add_turn("q1 ".ljust(400, "x"), "a1 ".ljust(3600, "y"))             # 100 + 900
    db.add_turn("q2 ".ljust(400, "x"), "a2 ".ljust(14000, "y"))            # 100 + 3500

    with patch.object(mem, "get_pg_client", return_value=db), \
         caplog.at_level(logging.WARNING, logger="langgraph.memory"):
        out = await mem.memory_node({"messages": []}, _config())

    assert [m.content[:2] for m in out["messages"]] == ["q2", "a2"]
    records = [r for r in caplog.records if r.getMessage() == "history_overflow_dropped"]
    assert len(records) == 1 and records[0].kept_tokens == 3600


@pytest.mark.asyncio
async def test_one_turn_over_the_ceiling_is_handed_on_whole(budgets, caplog):
    db = FakeDB()
    db.add_turn("q1 ".ljust(400, "x"), "a1 ".ljust(14000, "y"))            # 100 + 3500

    with patch.object(mem, "get_pg_client", return_value=db), \
         caplog.at_level(logging.WARNING, logger="langgraph.memory"):
        out = await mem.memory_node({"messages": []}, _config())

    assert [m.content[:2] for m in out["messages"]] == ["q1", "a1"]
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


async def _assert_no_hole(db: FakeDB, running: str | None = None) -> dict:
    """running = id of the question of the turn in progress, if it is written."""
    out = await mem.memory_node({"messages": []}, _config(user_message_id=running))
    raw = {m.content for m in out["messages"]}
    for m in db.messages:
        in_raw = m["content"] in raw
        in_summary = m["seq_id"] <= db.stm_up_to_seq
        # The running turn's question is the synthesizer's to add, and no
        # summary may take it while its answer is still to come.
        expected = 0 if m["id"] == running else 1
        assert in_raw + in_summary == expected, (
            f"seq {m['seq_id']}: raw={in_raw}, summaries={in_summary}")
    if out["messages"]:
        assert isinstance(out["messages"][0], HumanMessage)
    assert out["session_summary"] == ([db.stm_summary] if db.stm_summary else [])
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("summarise", [
    "before_the_question", "after_the_question", "after_the_answer"])
async def test_twelve_turn_session_has_no_hole(budgets, caplog, summarise):
    """housekeep starts with the turn, beside the write of its question, so
    compaction may land before that row, between the turn's two rows, or after
    both. Whichever it is, nothing falls between."""
    db = FakeDB()
    embed = MagicMock()
    embed.aembed_passage = AsyncMock(return_value=[0.1] * 384)

    async def fake_summary(previous, rows, session_id, usage=None):
        return f"summary of seq {rows[0]['seq_id']}..{rows[-1]['seq_id']}"

    ceiling = budgets["history_compact_at"] + budgets["history"]
    with patch.object(mem, "get_pg_client", return_value=db), \
         patch.object(summ, "get_pg_client", return_value=db), \
         patch.object(summ, "_summarize_rolling", side_effect=fake_summary), \
         caplog.at_level(logging.WARNING):
        for turn, (q_chars, a_chars) in enumerate(_TURNS, start=1):
            question_id = f"question-{turn}"
            if summarise == "before_the_question":
                await summ.compact_stm("s-1")
                await _assert_no_hole(db)
            db.add_message("user", f"q{turn}: " + "x" * q_chars, message_id=question_id)
            seen = await _assert_no_hole(db, question_id)   # what this turn's model is shown
            assert sum(len(m.content) // 4 for m in seen["messages"]) <= ceiling
            if summarise == "after_the_question":
                await summ.compact_stm("s-1")
                await _assert_no_hole(db, question_id)
            db.add_message("assistant", f"a{turn}: " + "y" * a_chars, token_count=6000)
            if summarise == "after_the_answer":
                await summ.compact_stm("s-1")
            await _assert_no_hole(db)

    assert db.stm_up_to_seq > 0, "the session never compacted"


@pytest.mark.asyncio
async def test_compaction_only_moves_the_marker_forward(budgets):
    """Ranges never overlap: the next compaction starts where the last stopped."""
    db = FakeDB()
    db.add_turn("q1 ".ljust(400, "x"), "a1 ".ljust(14000, "y"))
    db.add_turn("q2 ".ljust(400, "x"), "a2 ".ljust(14000, "y"))

    async def fake_summary(previous, rows, session_id, usage=None):
        return "S"

    with patch.object(summ, "get_pg_client", return_value=db), \
         patch.object(summ, "_summarize_rolling", side_effect=fake_summary):
        await summ.compact_stm("s-1")
        first_up_to = db.stm_up_to_seq
        assert first_up_to > 0
        await summ.compact_stm("s-1")
        assert db.stm_up_to_seq >= first_up_to


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


async def _compact_stm(rows, up_to=0, summary=None):
    pg = AsyncMock()
    pg.fetchrow = AsyncMock(
        return_value={"stm_summary": summary, "stm_up_to_seq": up_to})
    pg.fetch = AsyncMock(return_value=rows)
    pg.execute = AsyncMock(return_value="UPDATE 1")
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_summarize_rolling",
                      new=AsyncMock(return_value="S")) as roll:
        await summ.compact_stm("s-1")
    return roll, pg


@pytest.mark.asyncio
async def test_huge_token_count_with_short_content_does_not_trigger(budgets):
    rows = [{"seq_id": i, "role": "assistant", "content": "ok", "token_count": 50_000}
            for i in range(1, 9)]
    roll, _ = await _compact_stm(rows)
    roll.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_content_triggers_whatever_token_count_says(budgets):
    rows = _rows((100, 900), (100, 900), (100, 900))
    for r in rows:
        r["token_count"] = 1
    roll, _ = await _compact_stm(rows)
    roll.assert_awaited_once()


@pytest.mark.asyncio
async def test_at_the_threshold_nothing_is_summarised(budgets):
    roll, _ = await _compact_stm(_rows((500, 500), (500, 500)))       # 2000
    roll.assert_not_awaited()


@pytest.mark.asyncio
async def test_above_the_threshold_the_oldest_part_is_summarised(budgets):
    rows = _rows((500, 500), (500, 501))                               # 2001
    roll, pg = await _compact_stm(rows, up_to=41)
    roll.assert_awaited_once()
    # The new summary folds the old one plus the covered rows.
    assert roll.await_args.args[0] is None
    assert [r["seq_id"] for r in roll.await_args.args[1]] == [
        r["seq_id"] for r in rows if r["seq_id"] <= rows[1]["seq_id"]]
    sql, *args = pg.execute.await_args.args
    assert "stm_up_to_seq = $4" in " ".join(sql.split())
    assert args == ["s-1", "S", rows[1]["seq_id"], 41]


@pytest.mark.asyncio
async def test_newest_turns_stay_raw_while_they_fit_the_history_budget(budgets):
    rows = _rows((100, 800), (50, 250), (50, 250), (50, 250), (50, 250))   # 2100
    roll, _ = await _compact_stm(rows)
    # Three newest turns = 900 tokens; a fourth would make 1200.
    assert [r["seq_id"] for r in roll.await_args.args[1]][-1] == rows[3]["seq_id"]
    assert rows[4]["role"] == "user"


@pytest.mark.asyncio
async def test_the_cut_never_splits_a_turn(budgets):
    rows = _rows((100, 500), (100, 500), (100, 500), (100, 500))           # 2400
    roll, _ = await _compact_stm(rows)
    # Only the latest turn fits (600); the one before would make 1200.
    covered = roll.await_args.args[1]
    assert covered[-1]["seq_id"] == rows[5]["seq_id"]
    assert rows[5]["role"] == "assistant" and rows[6]["role"] == "user"


@pytest.mark.asyncio
async def test_the_latest_turn_stays_raw_even_when_it_alone_is_over_budget(budgets):
    rows = _rows((100, 400), (100, 2400))
    roll, _ = await _compact_stm(rows)
    assert roll.await_args.args[1][-1]["seq_id"] == rows[1]["seq_id"]

    roll, _ = await _compact_stm(_rows((100, 2400)))
    roll.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_question_still_waiting_for_its_answer_counts_as_the_latest_turn(budgets):
    rows = _rows((100, 700), (100, 700), (100, 500), (20, None))           # 2220
    roll, _ = await _compact_stm(rows)
    # Raw: the open question (20) + the turn before it (600); a third makes 1420.
    assert roll.await_args.args[1][-1]["seq_id"] == rows[3]["seq_id"]
    assert rows[4]["role"] == "user"


@pytest.mark.asyncio
async def test_compact_writes_the_cut_compare_and_set(budgets):
    pg = AsyncMock()
    pg.fetchrow = AsyncMock(
        return_value={"stm_summary": None, "stm_up_to_seq": 200})
    pg.fetch.return_value = _rows((400, 3800), (400, 3800))
    pg.execute = AsyncMock(return_value="UPDATE 1")
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_summarize_rolling",
                      new=AsyncMock(return_value="S")):
        await summ.compact_stm("s-1")

    sql, *args = pg.fetch.await_args.args
    assert args == ["s-1", 200]
    assert "seq_id > $2" in " ".join(sql.split())
    update_sql, *update_args = pg.execute.await_args.args
    assert "stm_up_to_seq = $4" in " ".join(update_sql.split())
    assert update_args == ["s-1", "S", 204, 200]


@pytest.mark.asyncio
async def test_compact_superseded_second_writer_writes_nothing(budgets, caplog):
    """Two compactions race on the same range: UPDATE 0 logs superseded."""
    import logging
    pg = AsyncMock()
    pg.fetchrow = AsyncMock(
        return_value={"stm_summary": None, "stm_up_to_seq": 200})
    pg.fetch.return_value = _rows((400, 3800), (400, 3800))
    pg.execute = AsyncMock(return_value="UPDATE 0")
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_summarize_rolling",
                      new=AsyncMock(return_value="S")):
        with caplog.at_level(logging.DEBUG):
            await summ.compact_stm("s-1")
    assert [r.getMessage() for r in caplog.records].count(
        "stm_compaction_superseded") == 1
    assert "stm_compacted" not in caplog.text


async def _compact_from(rows, up_to):
    pg = AsyncMock()
    pg.fetchrow = AsyncMock(
        return_value={"stm_summary": None, "stm_up_to_seq": up_to})
    pg.fetch = AsyncMock(return_value=rows)
    pg.execute = AsyncMock(return_value="UPDATE 1")
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_summarize_rolling",
                      new=AsyncMock(return_value="S")) as roll:
        await summ.compact_stm("s-1")
    return pg, roll


@pytest.mark.asyncio
async def test_a_finished_session_is_compacted_up_to_its_last_answer(budgets):
    """Its last question may belong to a turn that outlived its client: the
    answer lands after this read, and would be left with no question."""
    rows = _rows((500, 500), (500, 500), (500, None))
    pg, roll = await _compact_from(rows, 200)

    assert [r["seq_id"] for r in roll.await_args.args[1]] == [
        r["seq_id"] for r in rows[:4]]
    assert pg.execute.await_args.args[1:5] == ("s-1", "S", rows[3]["seq_id"], 200)
    assert rows[3]["role"] == "assistant" and rows[4]["role"] == "user"


@pytest.mark.asyncio
async def test_a_finished_session_of_unanswered_questions_is_left_alone(budgets):
    # One question alone is one turn: however long, nothing older to fold.
    pg, roll = await _compact_from(_rows((1200, None)), 200)

    roll.assert_not_awaited()
    pg.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_compaction_keeps_the_range_it_was_given(budgets):
    """_compaction_cut chose the cut; a question whose turn died may end it."""
    rows = _rows((500, 500), (500, 500), (500, None))
    pg, roll = await _compact_from(rows, 200)

    covered = roll.await_args.args[1]
    assert [r["seq_id"] for r in covered] == [r["seq_id"] for r in rows[:4]]
    assert covered[-1]["role"] == "assistant" and rows[4]["role"] == "user"


@pytest.mark.asyncio
async def test_rolling_summary_is_written_with_the_memory_role():
    llm = MagicMock()
    mock_ai = MagicMock(content=" A summary. ")
    mock_ai.usage_metadata = {}
    llm.ainvoke = AsyncMock(return_value=mock_ai)
    with patch.object(summ, "get_chat_model", return_value=llm) as get_model:
        text = await summ._summarize_rolling(
            "old summary",
            [{"seq_id": 1, "role": "user", "content": "my knee hurts"},
             {"seq_id": 2, "role": "assistant", "content": "try heel slides"}],
            "s-1")

    get_model.assert_called_once_with("memory")
    assert text == "A summary."
    prompt = llm.ainvoke.await_args.args[0]
    assert "old summary" in prompt
    assert "User: my knee hurts\nAssistant: try heel slides" in prompt
    assert "same language" in prompt.lower()
    assert "250 words" in prompt
