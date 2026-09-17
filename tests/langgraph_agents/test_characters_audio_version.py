# -*- coding: utf-8 -*-
"""Contract D golden vector — FastAPI half.

`routes_characters.audio_version()` and `infra/lambda/characters/handler.py`'s
copy must compute the exact same 12-hex-char value for the exact same input.
`tests/infra/test_characters_contract.py` pins the two implementations to each
other at the SOURCE level (an AST comparison of the function bodies) rather
than by importing both, because importing `routes_characters` there would drag
fastapi into the "Motion Queue + Infra" CI job, which installs only
infra/requirements-dev.txt and has no fastapi in it (T4/T5 broke this once by
importing both sides to compare them directly).

This file is the other half of that split: it runs the SAME golden vector
(literal-for-literal — `SAMPLE_STATIC_AUDIO` / `EXPECTED_AUDIO_VERSION` in
test_characters_contract.py) against the FastAPI copy, in a suite that already
depends on fastapi. Between the two files: same code (AST) + same output on a
concrete input (this file + the Lambda-side golden vector) is as strong a
guarantee as importing both side by side, without the collection-time cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agenticRAG"))

from langgraph_agents.api import routes_characters

# Byte-identical to SAMPLE_STATIC_AUDIO / EXPECTED_AUDIO_VERSION in
# tests/infra/test_characters_contract.py. Duplicated rather than imported:
# tests/infra and tests/langgraph_agents are collected by different CI jobs
# with different installed dependencies, and this pair only proves anything
# if each file can run to a fully independent verdict.
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


@pytest.mark.unit
def test_fastapi_audio_version_matches_the_golden_vector():
    """Same input → same 12 hex chars as the Lambda copy (see this file's
    docstring). Key order in the input must not matter (canonical sort)."""
    assert routes_characters.audio_version(SAMPLE_STATIC_AUDIO) == EXPECTED_AUDIO_VERSION
    assert routes_characters.audio_version(
        dict(reversed(list(SAMPLE_STATIC_AUDIO.items())))
    ) == EXPECTED_AUDIO_VERSION

    int(EXPECTED_AUDIO_VERSION, 16)  # hex, not a truncated repr of something else


@pytest.mark.unit
@pytest.mark.parametrize("empty", [{}, None, ""])
def test_fastapi_audio_version_of_empty_is_null(empty):
    """Contract D: no clips → audio_version is null (the frontend shows text
    only and reports no error)."""
    assert routes_characters.audio_version(empty) is None
