"""Integration tests — M.9 Step 15, rebuilt 3-axis model.

Requires DEEPSEEK_API_KEY (or live LLM endpoint).
Tests the full graph flow: memory → planner → retriever → synthesizer → grader.
"""

import os
import pytest

from langgraph_agents.graph import build_graph_async
from langgraph_agents.state import AgentState

HAS_LLM_KEY = bool(os.getenv("DEEPSEEK_API_KEY"))


def _base_state_config(**overrides):
    """Returns (initial_state, config) for graph.ainvoke."""
    state = {
        "messages": [],
        "errors": [],
        "retry_count": 0,
        "total_tokens": 0,
        "required_outputs": [],
        "resolved_query": "",
        "needs_retrieval": False,
        "needs_clarification": False,
    }
    config = {
        "configurable": {
            "user_id": "test-user",
            "session_id": "test-session",
            "query": overrides.pop("query", "Xin chào"),
            "persona_id": "anne",
            "request_id": "test-001",
            "web_search": False,
        }
    }
    state.update(overrides)
    return state, config


# ═══════════════════════════════════════════════════════════════════════════
# Live LLM tests (require DEEPSEEK_API_KEY)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.skipif(not HAS_LLM_KEY, reason="DEEPSEEK_API_KEY not set")
@pytest.mark.asyncio
async def test_chat_path_no_retrieval_no_tags():
    """Greeting → no retrieval, no tags → chat → END (fast-path D8)."""
    graph = await build_graph_async()
    state, config = _base_state_config(query="Xin chào")
    result = await graph.ainvoke(state, config=config)

    assert result["final_answer"], "final_answer should not be empty"
    # Chat path: no tags → grader skipped
    assert result.get("required_outputs", []) == []


@pytest.mark.integration
@pytest.mark.skipif(not HAS_LLM_KEY, reason="DEEPSEEK_API_KEY not set")
@pytest.mark.asyncio
async def test_safety_path_red_flag_no_retrieval():
    """Red-flag symptom → safety tags, NO retrieval; câu mở đầu do code phát."""
    graph = await build_graph_async()
    state, config = _base_state_config(query="Tôi bị đau ngực khi tập thể dục")
    result = await graph.ainvoke(state, config=config)

    assert result["final_answer"], "final_answer should not be empty"
    # Should have safety tags (D33: danger in query → planner detects)
    tags = result.get("required_outputs", [])
    assert "red_flag_screen" in tags or "referral_advice" in tags, \
        f"Expected safety tags, got {tags}"
    assert "chưa được kiểm chứng" not in result["final_answer"]
    assert "has not been verified" not in result["final_answer"]


@pytest.mark.integration
@pytest.mark.skipif(not HAS_LLM_KEY, reason="DEEPSEEK_API_KEY not set")
@pytest.mark.asyncio
async def test_exercise_path_with_retrieval():
    """Exercise query → needs_retrieval + exercise tags → full pipeline."""
    graph = await build_graph_async()
    state, config = _base_state_config(query="Bài tập cho đau lưng dưới")
    result = await graph.ainvoke(state, config=config)

    assert result["final_answer"], "final_answer should not be empty"
    # Should have exercise-related tags
    tags = result.get("required_outputs", [])
    exercise_tags = {"exercise_protocol", "exercise_steps", "scope_disclaimer"}
    assert any(t in exercise_tags for t in tags), \
        f"Expected exercise tags, got {tags}"


@pytest.mark.integration
@pytest.mark.skipif(not HAS_LLM_KEY, reason="DEEPSEEK_API_KEY not set")
@pytest.mark.asyncio
async def test_clarify_path():
    """Ambiguous query → clarify, no retrieval."""
    graph = await build_graph_async()
    state, config = _base_state_config(query="bài tập")
    result = await graph.ainvoke(state, config=config)

    assert result["final_answer"], "final_answer should not be empty"
    # Should trigger clarification (missing body region)
    assert result.get("needs_clarification") or "?" in result["final_answer"] or "bạn" in result["final_answer"].lower(), \
        "Expected clarification response"



# ═══════════════════════════════════════════════════════════════════════════
# Unit tests (no LLM needed) — planner output structure
# ═══════════════════════════════════════════════════════════════════════════

class TestPlanOutput:
    """Verify PlanOutput Pydantic model structure (M.1 3-axis)."""

    def test_plan_output_defaults(self):
        from langgraph_agents.nodes.planner import PlanOutput
        plan = PlanOutput()
        assert plan.required_outputs == []
        assert plan.resolved_query == ""
        assert plan.needs_retrieval is False
        assert plan.needs_clarification is False

    def test_plan_output_3_axis(self):
        from langgraph_agents.nodes.planner import PlanOutput
        plan = PlanOutput(
            required_outputs=["exercise_protocol", "scope_disclaimer"],
            resolved_query="bài tập cho L4-L5",
            needs_retrieval=True,
            needs_clarification=False,
        )
        assert len(plan.required_outputs) == 2
        assert "exercise_protocol" in plan.required_outputs
        assert plan.needs_retrieval is True

    def test_plan_output_json_mode(self):
        """Verify PlanOutput can serialize/deserialize (json_mode compat)."""
        from langgraph_agents.nodes.planner import PlanOutput
        plan = PlanOutput(
            required_outputs=["red_flag_screen", "referral_advice"],
            resolved_query="đau ngực khi tập",
            needs_retrieval=False,
        )
        d = plan.model_dump()
        plan2 = PlanOutput(**d)
        assert plan2.required_outputs == plan.required_outputs
        assert plan2.resolved_query == plan.resolved_query
        assert "needs_motion" not in d




# ═══════════════════════════════════════════════════════════════════════════
# Unit tests — Synthesizer mode derivation (D29, no LLM)
# ═══════════════════════════════════════════════════════════════════════════

class TestModeDerivation:
    """Verify _derive_mode logic (D29: mode emerges from signals)."""

    def test_chat_mode(self):
        from langgraph_agents.nodes.synthesizer import _derive_mode
        state = {
            "needs_clarification": False,
            "required_outputs": [],
            "messages": [],
        }
        assert _derive_mode(state) == "chat"

    def test_clarify_static(self):
        from langgraph_agents.nodes.synthesizer import _derive_mode
        state = {
            "needs_clarification": True,
            "required_outputs": [],
            "messages": [],
        }
        assert _derive_mode(state) == "clarify"

    def test_clinical_no_source_is_chat(self):
        from langgraph_agents.nodes.synthesizer import _derive_mode
        state = {
            "needs_clarification": False,
            "required_outputs": ["exercise_protocol", "scope_disclaimer"],
            "messages": [],  # No ToolMessages
        }
        assert _derive_mode(state) == "chat"

    def test_synthesize_has_tool_results(self):
        from langgraph_agents.nodes.synthesizer import _derive_mode
        from langchain_core.messages import ToolMessage
        tool_msg = ToolMessage(
            content='{"found": true, "results": [{"content": "test"}]}',
            tool_call_id="test-001",
            name="kb_search",
        )
        state = {
            "needs_clarification": False,
            "required_outputs": ["exercise_protocol"],
            "messages": [tool_msg],
        }
        assert _derive_mode(state) == "synthesize"


