"""S1: prompt chọn tool không nhắc tên tool nào; mô tả tool viết cho model.

Lý do: bảng luật viết tay (TOOLS AVAILABLE + DECISION RULES) bắt mỗi tool mới
phải sửa prompt. Từ S1 mỗi tool tự mô tả; prompt chỉ còn nguyên tắc chung.
"""

import re

import pytest

from langgraph_agents.shared.lang import _VN_EXCLUSIVE

_VN_CHARS = frozenset(_VN_EXCLUSIVE + _VN_EXCLUSIVE.upper())


async def _selector_tools_and_prompt(web_search_enabled: bool,
                                     allow_web_fallback: bool = True):
    from langgraph_agents.nodes.retriever_agent import (
        _build_retriever_system_prompt, _build_tools,
    )

    # Hermetic: test khác có thể để mock trong cache MCP toàn cục
    # (test_mcp_breaker_records_success_and_closes từng làm vậy) — test này
    # đo danh sách tool THẬT nên dọn cache trước.
    from langgraph_agents.mcp.client import close_mcp_client

    await close_mcp_client()
    tools = await _build_tools(web_search_enabled=web_search_enabled,
                               persona_id="anne")
    prompt = _build_retriever_system_prompt(
        web_search_enabled=web_search_enabled,
        allow_web_fallback=allow_web_fallback,
        retry_note="",
        required_outputs="exercise_steps",
        resolved_query="shoulder stretch",
    )
    return tools, prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_selector_prompt_names_no_tool():
    """Với mọi tool đưa cho model: tên tool không có trong system prompt.

    Viết theo vòng lặp trên danh sách tool thật, không liệt kê tay — thêm
    tool mới (in-process/MCP) không phải sửa test này lẫn prompt.
    """
    from langgraph_agents.nodes import retriever_agent as ra
    from langgraph_agents.mcp.client import close_mcp_client

    await close_mcp_client()
    for web_on in (False, True):
        tools = await ra._build_tools(web_search_enabled=web_on,
                                      persona_id="anne")
        assert tools, f"tool list empty (web_search_enabled={web_on})"
        for fallback in (False, True):
            prompt = ra._build_retriever_system_prompt(
                web_search_enabled=web_on,
                allow_web_fallback=fallback,
                retry_note="",
                required_outputs="exercise_steps",
                resolved_query="shoulder stretch",
            )
            for t in tools:
                assert t.name not in prompt, (
                    f"tool {t.name!r} leaked into selector prompt "
                    f"(web_on={web_on}, fallback={fallback})"
                )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_descriptions_are_for_the_model():
    """Mô tả mỗi tool: không rỗng, ≤400 ký tự, không mã D, không dấu
    tiếng Việt, không Args:/Returns:. Ghi chú dev nằm trong docstring/comment."""
    from langgraph_agents.nodes import retriever_agent as ra
    from langgraph_agents.mcp.client import close_mcp_client

    await close_mcp_client()
    tools = await ra._build_tools(web_search_enabled=True, persona_id="anne")
    assert tools
    for t in tools:
        desc = t.description or ""
        assert desc.strip(), f"tool {t.name!r} has empty description"
        assert len(desc) <= 400, f"tool {t.name!r} description too long ({len(desc)})"
        assert not re.search(r"D\d+", desc), (
            f"tool {t.name!r} description leaks dev decision code"
        )
        assert not any(c in _VN_CHARS for c in desc), (
            f"tool {t.name!r} description has Vietnamese diacritics"
        )
        assert "Args:" not in desc and "Returns:" not in desc, (
            f"tool {t.name!r} description leaks dev schema notes"
        )
