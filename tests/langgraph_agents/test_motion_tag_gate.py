"""S2: Kimodo chạy theo tag motion_descriptor; cờ needs_motion bị xóa.

Vì sao: planner đã nói "người dùng xin xem động tác" bằng tag
motion_descriptor — cờ needs_motion nói lại đúng điều đó lần thứ hai.
"""

import pytest


@pytest.mark.unit
class TestWantsMotion:
    """Một hàm duy nhất quyết định có chạy Kimodo không (S2)."""

    def test_tag_present_runs(self):
        from langgraph_agents.routing import wants_motion

        assert wants_motion({"required_outputs": ["motion_descriptor"]}) is True

    def test_tag_absent_skips(self):
        from langgraph_agents.routing import wants_motion

        assert wants_motion({"required_outputs": ["exercise_steps"]}) is False
        assert wants_motion({"required_outputs": []}) is False

    def test_missing_required_outputs_skips(self):
        from langgraph_agents.routing import wants_motion

        assert wants_motion({}) is False


@pytest.mark.unit
class TestPlanOutputHasNoMotionFlag:
    """PlanOutput không còn trường needs_motion; prompt không còn chuỗi đó (S2)."""

    def test_model_rejects_motion_flag(self):
        from langgraph_agents.nodes.planner import PlanOutput

        plan = PlanOutput(required_outputs=[], resolved_query="hi")
        assert "needs_motion" not in plan.model_dump()
        assert not hasattr(plan, "needs_motion")

    def test_prompt_has_no_motion_flag(self):
        from langgraph_agents.nodes.planner import _PLANNER_SYSTEM_PROMPT

        assert "needs_motion" not in _PLANNER_SYSTEM_PROMPT

    def test_prompt_defines_motion_tag_strictly(self):
        from langgraph_agents.nodes.planner import _PLANNER_SYSTEM_PROMPT

        assert "Asking how to do" in _PLANNER_SYSTEM_PROMPT
        assert "whether you can" in _PLANNER_SYSTEM_PROMPT


@pytest.mark.unit
class TestMotionTagRouting:
    """Cả hai route_after_planner (routing + graph) đọc tag, giữ thứ tự ưu tiên."""

    @pytest.mark.parametrize("route_path", [
        "langgraph_agents.routing.route_after_planner",
        "langgraph_agents.graph.route_after_planner",
    ])
    def test_tag_without_retrieval_goes_kimodo(self, route_path):
        import importlib

        mod_name, fn_name = route_path.rsplit(".", 1)
        route = getattr(importlib.import_module(mod_name), fn_name)
        state = {"messages": [], "errors": [], "retry_count": 0,
                 "total_tokens": 0, "required_outputs": ["motion_descriptor"],
                 "needs_retrieval": False}
        assert route(state) == "kimodo"

    @pytest.mark.parametrize("route_path", [
        "langgraph_agents.routing.route_after_planner",
        "langgraph_agents.graph.route_after_planner",
    ])
    def test_no_tag_goes_synthesizer(self, route_path):
        import importlib

        mod_name, fn_name = route_path.rsplit(".", 1)
        route = getattr(importlib.import_module(mod_name), fn_name)
        state = {"messages": [], "errors": [], "retry_count": 0,
                 "total_tokens": 0, "required_outputs": [],
                 "needs_retrieval": False}
        assert route(state) == "synthesizer"

    def test_clarify_wins_over_motion_tag(self):
        """needs_clarification bật → synthesizer, Kimodo không chạy (giữ HEAD)."""
        import langgraph_agents.graph as graph_mod
        import langgraph_agents.routing as routing_mod

        for route in (routing_mod.route_after_planner,
                      graph_mod.route_after_planner):
            state = {"messages": [], "errors": [], "retry_count": 0,
                     "total_tokens": 0,
                     "required_outputs": ["motion_descriptor"],
                     "needs_retrieval": False, "needs_clarification": True}
            assert route(state) == "synthesizer"

    def test_retriever_chain_uses_tag(self):
        from langgraph_agents.routing import route_after_retriever, route_after_tools

        tagged = {"messages": [], "errors": [], "retry_count": 0,
                  "total_tokens": 0, "retriever_rounds": 0,
                  "required_outputs": ["motion_descriptor"]}
        untagged = {**tagged, "required_outputs": []}
        assert route_after_retriever(tagged) == "kimodo"
        assert route_after_retriever(untagged) == "synthesizer"
        assert route_after_tools(tagged) == "kimodo"
        assert route_after_tools(untagged) == "synthesizer"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_graph_runs_kimodo_once_with_tag_regardless_of_retrieval():
    """Graph với LLM giả: có tag → kimodo chạy đúng một lần (retrieval on/off)."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from langchain_core.messages import AIMessage, ToolMessage

    from langgraph_agents.graph import build_graph_async
    from langgraph_agents.nodes.planner import PlanOutput

    async def _run(needs_retrieval: bool) -> int:
        plan = PlanOutput(
            required_outputs=["scope_disclaimer", "motion_descriptor"],
            resolved_query="squat movement", needs_retrieval=needs_retrieval,
        )
        mock_struct = MagicMock()
        mock_struct.ainvoke = AsyncMock(return_value=plan)
        mock_planner_llm = MagicMock()
        mock_planner_llm.with_structured_output = MagicMock(
            return_value=mock_struct)

        kb_ai = AIMessage(content="", tool_calls=[])
        mock_bound = MagicMock()
        mock_bound.ainvoke = AsyncMock(return_value=kb_ai)
        mock_retriever_llm = MagicMock()
        mock_retriever_llm.bind_tools = MagicMock(return_value=mock_bound)

        synth_llm = MagicMock()
        synth_llm.ainvoke = AsyncMock(return_value=AIMessage(
            content="giơ tay phải lên — khớp vai và khuỷu"))

        kimodo_runs: list = []

        async def fake_kimodo(state, config):
            kimodo_runs.append(1)
            return {"messages": [ToolMessage(
                content='{"state": "queued", "job_id": "j1", "prompt": "squat"}',
                tool_call_id="kimodo_motion", name="generate_motion")]}

        import langgraph_agents.graph as graph_mod

        state = {"messages": [], "errors": [], "retry_count": 0,
                 "total_tokens": 0}
        config = {"configurable": {
            "user_id": "u1", "session_id": str(uuid.uuid4()),
            "query": "show me a squat", "persona_id": "anne",
            "request_id": "r-s2", "web_search": False, "locale": "en",
        }}
        with patch("langgraph_agents.nodes.planner.get_chat_model",
                   return_value=mock_planner_llm), \
             patch("langgraph_agents.nodes.retriever_agent.get_chat_model",
                   return_value=mock_retriever_llm), \
             patch("langgraph_agents.nodes.synthesizer.get_chat_model",
                   return_value=synth_llm), \
             patch("langgraph_agents.nodes.synthesizer.get_stream_writer",
                   side_effect=RuntimeError("no stream")), \
             patch.object(graph_mod, "kimodo_node", new=fake_kimodo):
            graph = await build_graph_async()
            await graph.ainvoke(state, config=config)
        return len(kimodo_runs)

    assert await _run(True) == 1
    assert await _run(False) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_graph_skips_kimodo_without_tag():
    """Không có tag → kimodo không chạy (kể cả khi retrieval bật)."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from langchain_core.messages import AIMessage

    from langgraph_agents.graph import build_graph_async
    from langgraph_agents.nodes.planner import PlanOutput
    import langgraph_agents.graph as graph_mod

    plan = PlanOutput(required_outputs=["evidence_citation"],
                      resolved_query="squat", needs_retrieval=True)
    mock_struct = MagicMock()
    mock_struct.ainvoke = AsyncMock(return_value=plan)
    mock_planner_llm = MagicMock()
    mock_planner_llm.with_structured_output = MagicMock(
        return_value=mock_struct)

    kb_ai = AIMessage(content="", tool_calls=[])
    mock_bound = MagicMock()
    mock_bound.ainvoke = AsyncMock(return_value=kb_ai)
    mock_retriever_llm = MagicMock()
    mock_retriever_llm.bind_tools = MagicMock(return_value=mock_bound)

    synth_llm = MagicMock()
    synth_llm.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))

    never_kimodo = AsyncMock(
        side_effect=AssertionError("kimodo must not run without the tag"))

    state = {"messages": [], "errors": [], "retry_count": 0, "total_tokens": 0}
    config = {"configurable": {
        "user_id": "u1", "session_id": str(uuid.uuid4()),
        "query": "how to squat", "persona_id": "anne",
        "request_id": "r-s2b", "web_search": False, "locale": "en",
    }}
    with patch("langgraph_agents.nodes.planner.get_chat_model",
               return_value=mock_planner_llm), \
         patch("langgraph_agents.nodes.retriever_agent.get_chat_model",
               return_value=mock_retriever_llm), \
         patch("langgraph_agents.nodes.synthesizer.get_chat_model",
               return_value=synth_llm), \
         patch("langgraph_agents.nodes.synthesizer.get_stream_writer",
               side_effect=RuntimeError("no stream")), \
         patch.object(graph_mod, "kimodo_node", new=never_kimodo):
        graph = await build_graph_async()
        await graph.ainvoke(state, config=config)
        never_kimodo.assert_not_called()


@pytest.mark.unit
class TestGraderMotionRetryPinned:
    """Chốt hành vi retry của grader với tag motion_descriptor ở HEAD.

    S2 không đụng grader — test này phải xanh y hệt trước và sau.
    motion_descriptor là quality tag: thiếu → retry, đủ → pass.
    """

    def test_motion_missing_means_retry(self):
        from langgraph_agents.nodes.grader import _grade_tags

        out = _grade_tags("Bài tập squat tốt cho chân.",
                          ["motion_descriptor"])
        assert out["result"] == "retry"
        assert out["quality_missing"] == ["motion_descriptor"]
        assert out["safety_missing"] == []

    def test_motion_present_passes_quality(self):
        from langgraph_agents.nodes.grader import _grade_tags

        out = _grade_tags(
            "Giơ tay phải lên qua đầu — khớp vai và khuỷu cùng làm việc. "
            "(Nguyen 2024)",
            ["motion_descriptor", "evidence_citation"],
        )
        assert out["quality_missing"] == []
