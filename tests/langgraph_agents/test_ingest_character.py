"""Chunking cho ingest character (plan T8c) — thuần parser, không model/DB."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "ingest_character_pgvector.py"
)
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "anne_sheet_fixture.md"


def _load():
    spec = importlib.util.spec_from_file_location("ingest_character", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_sections_split_on_headers_with_titles():
    mod = _load()
    chunks = mod.parse_persona_file(_FIXTURE, "sheet")
    titles = [c["title"] for c in chunks]
    assert "Appearance" in titles
    assert "Footwear" in titles
    assert all(c["kind"] == "sheet" for c in chunks)
    appearance = next(c for c in chunks if c["title"] == "Appearance")
    assert "short hair" in appearance["content"]


@pytest.mark.unit
def test_overlong_section_splits_on_paragraphs():
    mod = _load()
    chunks = mod.parse_persona_file(_FIXTURE, "sheet")
    tastes = [c for c in chunks if c["title"] == "Tastes"]
    assert len(tastes) >= 2, "long section must split further"
    assert all(len(c["content"]) <= mod.MAX_CHUNK_CHARS for c in tastes)


@pytest.mark.unit
def test_load_character_chunks_missing_slug_gives_empty():
    mod = _load()
    assert mod.load_character_chunks("no_such_character_xyz") == []
