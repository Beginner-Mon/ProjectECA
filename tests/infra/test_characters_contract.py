"""The two /characters implementations must agree on what they return.

There are two of them and there is no way to make there be one: production runs
infra/lambda/characters/handler.py on pg8000, deployed as a CDK asset directory
that contains nothing else (character_stack.py), while
agenticRAG/langgraph_agents/api/routes_characters.py runs asyncpg inside the
FastAPI app so that a single-port local dev server can serve the catalog. Neither
can import the other.

They drifted the first time nobody was checking. 863458d trimmed the list
response from nine columns to five for the picker grid — and touched only the
FastAPI copy, which its own module docstring calls a local shim. Production kept
returning every column, so the optimisation that was measured and merged had no
effect at all where it was supposed to have one.

Reading the column lists out of the source is enough to catch that: the drift is
always a column added or removed on one side. Most of this file parses source
with `ast` and imports neither module — the Lambda's dependencies (pg8000,
boto3) are built at deploy time and are not installed here, and the FastAPI
module needs fastapi, which the CI job that runs this file ("Motion Queue +
Infra") never installs (infra/requirements-dev.txt only).

T4/T5 briefly broke that rule for the `audio_version()` contract (imported
BOTH modules to compare them directly) — that collected fine wherever fastapi
happened to already be installed and failed collection everywhere else. Fixed
by going back to the rule: `audio_version()`'s DRIFT check is AST-only
(`test_both_implementations_compute_audio_version_identically`), same as the
column lists and the clip allowlist. Its BEHAVIOUR is a golden vector checked
against each implementation independently — the Lambda half here (stubbing
its deps the same way test_characters_handler_payload.py does, importing only
`handler`, never `routes_characters`), the FastAPI half in
tests/langgraph_agents/test_characters_audio_version.py, which already has
fastapi. Both pin the SAME literal expected hash.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LAMBDA = _ROOT / "infra" / "lambda" / "characters" / "handler.py"
_FASTAPI = _ROOT / "agenticRAG" / "langgraph_agents" / "api" / "routes_characters.py"


def _string_constants(path: Path) -> dict[str, str]:
    """Every module-level `NAME = "..."` in a file, without importing it.

    Implicit string concatenation across lines parses to one Constant, so the
    parenthesised form one file uses and the triple-quoted form the other uses
    both come back as plain strings.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        if isinstance(value, str):
            found[target.id] = value
    return found


def _columns(source: str) -> set[str]:
    return {part.strip() for part in source.split(",") if part.strip()}


@pytest.fixture(scope="module")
def constants() -> tuple[dict[str, str], dict[str, str]]:
    return _string_constants(_LAMBDA), _string_constants(_FASTAPI)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["_PUBLIC_COLUMNS", "_PUBLIC_COLUMNS_LITE"])
def test_both_implementations_declare_the_same_columns(constants, name):
    lambda_consts, fastapi_consts = constants

    assert name in lambda_consts, f"{_LAMBDA.name} no longer defines {name}"
    assert name in fastapi_consts, f"{_FASTAPI.name} no longer defines {name}"

    in_lambda = _columns(lambda_consts[name])
    in_fastapi = _columns(fastapi_consts[name])

    assert in_lambda == in_fastapi, (
        f"{name} has drifted between the two /characters implementations.\n"
        f"  only in the Lambda (production): {sorted(in_lambda - in_fastapi)}\n"
        f"  only in the FastAPI shim (dev):  {sorted(in_fastapi - in_lambda)}\n"
        "Both must be changed together — see this file's docstring."
    )


@pytest.mark.unit
def test_lite_is_a_strict_subset_of_full(constants):
    """The list response may narrow the detail response, never extend it."""
    for consts, path in zip(constants, (_LAMBDA, _FASTAPI)):
        lite = _columns(consts["_PUBLIC_COLUMNS_LITE"])
        full = _columns(consts["_PUBLIC_COLUMNS"])
        assert lite < full, (
            f"{path.name}: the lite column set must be a proper subset of the "
            f"full one; extra columns: {sorted(lite - full)}"
        )


@pytest.mark.unit
def test_lite_omits_the_columns_the_card_grid_does_not_use(constants):
    """What the narrowing was for.

    `vrm_url` points at a 9-17 MB model nobody downloads from a card, and
    `ui_strings` is the whole chat-surface copy for a character. Both are served
    by /characters/{slug} once one has been picked. `vrm_metadata` stays: the
    card greys itself out for models the device cannot run.
    """
    for consts, path in zip(constants, (_LAMBDA, _FASTAPI)):
        lite = _columns(consts["_PUBLIC_COLUMNS_LITE"])
        for column in ("vrm_url", "ui_strings", "voice_language"):
            assert column not in lite, f"{path.name}: {column} is back in the list response"
        assert "vrm_metadata" in lite, f"{path.name}: the card needs vrm_metadata to check compatibility"


@pytest.mark.unit
def test_persona_is_never_public():
    """persona is the LLM system prompt. It must not appear in either list."""
    for path in (_LAMBDA, _FASTAPI):
        consts = _string_constants(path)
        for name in ("_PUBLIC_COLUMNS", "_PUBLIC_COLUMNS_LITE"):
            assert "persona" not in _columns(consts[name]), (
                f"{path.name}: {name} exposes the system prompt"
            )


def _tuple_constants(path: Path) -> dict[str, tuple]:
    """Every module-level `NAME = (...)` tuple in a file, without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, tuple] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        if isinstance(value, tuple):
            found[target.id] = value
    return found


@pytest.mark.unit
def test_both_implementations_allow_the_same_clips():
    """T4: the /audio allowlist must be identical in both implementations —
    same mechanism as the column lists above, for the same reason. A clip the
    Lambda rejects but local dev serves (or vice versa) is a bug that only
    appears after deploy."""
    lambda_clips = set(_tuple_constants(_LAMBDA).get("_ALLOWED_CLIPS", ()))
    fastapi_clips = set(_tuple_constants(_FASTAPI).get("_ALLOWED_CLIPS", ()))

    assert lambda_clips, f"{_LAMBDA.name} no longer defines _ALLOWED_CLIPS"
    assert fastapi_clips, f"{_FASTAPI.name} no longer defines _ALLOWED_CLIPS"
    assert lambda_clips == fastapi_clips, (
        "the /audio allowlist has drifted between the two implementations.\n"
        f"  only in the Lambda (production): {sorted(lambda_clips - fastapi_clips)}\n"
        f"  only in the FastAPI shim (dev):  {sorted(fastapi_clips - lambda_clips)}"
    )


@pytest.mark.unit
def test_allowlist_is_the_four_greeting_slots():
    """Contract B fixes the set: greeting.morning/afternoon/evening/night."""
    for path in (_LAMBDA, _FASTAPI):
        clips = set(_tuple_constants(path).get("_ALLOWED_CLIPS", ()))
        assert clips == {
            "greeting.morning",
            "greeting.afternoon",
            "greeting.evening",
            "greeting.night",
        }, f"{path.name}: _ALLOWED_CLIPS is not the contract-B set: {sorted(clips)}"


def _function_body_dump(path: Path, name: str) -> str:
    """AST dump of a top-level function's body, docstring stripped.

    T4/T5 (894ab07d/0d3b8663) replaced this file's original AST-only design
    with an import of BOTH `handler` and `langgraph_agents.api.routes_characters`
    — the latter needs fastapi, which broke this module's own stated rule
    ("these tests do not connect to a database and do not import either
    module") and, with it, the "Motion Queue + Infra" CI job, which installs
    only infra/requirements-dev.txt and has no fastapi in it. Comparing the
    parsed function bodies instead needs no third-party dependency from
    either side — same technique `_string_constants`/`_tuple_constants`
    above already use for the column lists and the clip allowlist.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = list(node.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]  # docstrings differ on purpose; code must not
            assert body, f"{path.name}: {name}() has no body once its docstring is stripped"
            return "\n".join(ast.dump(stmt, annotate_fields=False) for stmt in body)
    raise AssertionError(f"{path.name}: no top-level function named {name!r}")


@pytest.mark.unit
def test_both_implementations_compute_audio_version_identically():
    """Contract D drift guard: same code on both sides, not just same output
    on one sample. Behaviour coverage (the golden vector) lives separately —
    the Lambda half in this file (`test_characters_contract_audio_version` /
    test_characters_handler_payload.py's stubbing pattern), the FastAPI half
    in tests/langgraph_agents/test_characters_audio_version.py, both pinned
    to the SAME literal expected hash so they cannot silently drift apart
    either."""
    lambda_body = _function_body_dump(_LAMBDA, "audio_version")
    fastapi_body = _function_body_dump(_FASTAPI, "audio_version")
    assert lambda_body == fastapi_body, (
        "audio_version() has drifted between the two implementations — same "
        "json.loads/json.dumps/sha256 slice must appear on both sides."
    )


# Contract D golden vector — the exact same literal is asserted against the
# FastAPI copy in tests/langgraph_agents/test_characters_audio_version.py.
# Key order in the input must not matter (canonical sort inside the function).
SAMPLE_STATIC_AUDIO = {
    "greeting.evening": {
        "en": {
            "key": "characters/anne/audio/1b7e08d3.ogg",
            "sha256": "b" * 64,
            "text_sha256": "c" * 64,
        },
    },
    "greeting.morning": {
        "vi": {
            "key": "characters/anne/audio/9f2c1a4b.ogg",
            "sha256": "a" * 64,
            "text_sha256": "d" * 64,
        },
    },
}
EXPECTED_AUDIO_VERSION = "b60a03ed1e7c"


@pytest.fixture(scope="module")
def lambda_audio_version():
    """Import audio_version() from the Lambda copy only, deps stubbed — same
    pattern as test_characters_handler_payload.py's `handler_module` fixture.
    Deliberately does NOT import routes_characters (see _function_body_dump's
    docstring above)."""
    import sys
    from unittest.mock import MagicMock

    lambda_root = _ROOT / "infra" / "lambda"
    sys.path.insert(0, str(lambda_root / "layer"))
    sys.path.insert(0, str(lambda_root / "characters"))

    pg8000 = MagicMock()
    pg8000.dbapi = MagicMock()
    sys.modules.setdefault("pg8000", pg8000)
    sys.modules.setdefault("pg8000.dbapi", pg8000.dbapi)
    sys.modules.setdefault("boto3", MagicMock())

    import handler
    return handler.audio_version


@pytest.mark.unit
def test_lambda_audio_version_matches_the_golden_vector(lambda_audio_version):
    """Contract D, Lambda half. See test_characters_audio_version.py in
    tests/langgraph_agents/ for the FastAPI half — same literal expected
    value, asserted independently since this file cannot import fastapi."""
    assert lambda_audio_version(SAMPLE_STATIC_AUDIO) == EXPECTED_AUDIO_VERSION
    assert lambda_audio_version(
        dict(reversed(list(SAMPLE_STATIC_AUDIO.items())))
    ) == EXPECTED_AUDIO_VERSION  # key order must not matter

    int(EXPECTED_AUDIO_VERSION, 16)  # hex, not a truncated repr of something else


@pytest.mark.unit
@pytest.mark.parametrize("empty", [{}, None, ""])
def test_lambda_audio_version_of_empty_is_null(lambda_audio_version, empty):
    """Contract D: no clips → audio_version is null (the frontend shows text
    only and reports no error)."""
    assert lambda_audio_version(empty) is None
