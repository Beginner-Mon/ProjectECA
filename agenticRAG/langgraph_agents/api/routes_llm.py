"""BYOK capability advertisement.

GET /llm/config tells the frontend whether it may offer "use your own key":
always on once this route exists (the frontend treats a missing route as
"backend too old, BYOK off"), plus whether local endpoints are accepted and
what the ECA fallback itself can do.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from langgraph_agents.api.auth import current_user_id
from langgraph_agents.llm_byok import ECA_CAPABILITIES, local_endpoints_allowed

router = APIRouter(tags=["llm"])


@router.get("/llm/config")
async def llm_config(uid: str = Depends(current_user_id)) -> dict:
    _ = uid  # identity is the gate; the payload carries no per-user data.
    return {
        "byok": True,
        "local_endpoints": local_endpoints_allowed(),
        "eca": {"capabilities": list(ECA_CAPABILITIES)},
    }
