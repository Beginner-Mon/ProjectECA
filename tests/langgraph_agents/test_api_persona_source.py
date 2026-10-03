"""VVA_PERSONA_SOURCE — backend local đọc persona từ file (plan vòng 3, Task 2)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import langgraph_agents.api.main as main_mod


async def _drive_lifespan():
    async with main_mod.lifespan(MagicMock()):
        pass


def _mocked_lifespan():
    return (
        patch.object(main_mod, "verify_auth_config"),
        patch.object(main_mod, "run_preflight"),
        patch.object(main_mod, "build_graph_async",
                     new=AsyncMock(return_value=MagicMock())),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_files_value_skips_db_preload(monkeypatch):
    """VVA_PERSONA_SOURCE=files → preload_personas_from_db không được gọi."""
    monkeypatch.setenv("VVA_PERSONA_SOURCE", "files")
    p_auth, p_preflight, p_graph = _mocked_lifespan()
    with p_auth, p_preflight, p_graph, \
         patch.object(main_mod, "preload_personas_from_db",
                      new=AsyncMock(return_value=4)) as mock_preload:
        await _drive_lifespan()
    mock_preload.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unset_calls_db_preload(monkeypatch):
    """Không đặt → hành vi như hiện tại: preload từ DB."""
    monkeypatch.delenv("VVA_PERSONA_SOURCE", raising=False)
    p_auth, p_preflight, p_graph = _mocked_lifespan()
    with p_auth, p_preflight, p_graph, \
         patch.object(main_mod, "preload_personas_from_db",
                      new=AsyncMock(return_value=4)) as mock_preload:
        await _drive_lifespan()
    mock_preload.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_other_value_calls_db_preload(monkeypatch):
    """Giá trị khác → hành vi như hiện tại."""
    monkeypatch.setenv("VVA_PERSONA_SOURCE", "db")
    p_auth, p_preflight, p_graph = _mocked_lifespan()
    with p_auth, p_preflight, p_graph, \
         patch.object(main_mod, "preload_personas_from_db",
                      new=AsyncMock(return_value=4)) as mock_preload:
        await _drive_lifespan()
    mock_preload.assert_called_once_with()
