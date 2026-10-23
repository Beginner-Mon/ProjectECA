"""CRUD routes — sessions and user memory. No graph, no LLM, no embeddings.

These are the routes that only ever touch PostgreSQL, split out so they can be
served by a small Lambda instead of a process that imports torch at boot. They
are defined once, here, and mounted by both apps:

    api/crud_app.py  → the Lambda; these routes and nothing else
    api/main.py      → the agent; these routes plus /chat and friends

Defining them once is the point. The previous attempt at this split hand-wrote
parallel handlers under infra/lambda/, and they drifted from the originals
before ever running: infra/lambda/resume_session/handler.py orders messages by
created_at with no seq_id tie-breaker, which is exactly the bug that
db/session_store.py carries a comment warning about (both rows of a turn are
written in one executemany and share a timestamp, so the reply sorts ahead of
the question).

Identity is never taken from the request. Every route below gets its user from
Depends(current_user_id), which reads it from the verified token — see
api/auth.py. There is deliberately no user_id path or query parameter to trust.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from langgraph_agents.api.auth import current_user_id
from langgraph_agents.api.schemas import (
    SessionListItem, SessionListResponse, SessionResumeResponse,
    UserMemoryCreate, UserMemoryItem, UserMemoryListResponse, UserMemoryUpdate,
)
from langgraph_agents.db.session_store import (
    list_user_sessions, load_session_messages,
)
from langgraph_agents.shared import get_pg_client
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.api.crud")

router = APIRouter(tags=["crud"])


# ── Sessions ───────────────────────────────────────────────────────────────────


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(uid: str = Depends(current_user_id), limit: int = 50):
    rows = await list_user_sessions(user_id=uid, limit=limit)
    return SessionListResponse(
        sessions=[SessionListItem(**r) for r in rows],
        total=len(rows),
    )


@router.get("/sessions/{session_id}", response_model=SessionResumeResponse)
async def get_session(
    session_id: str,
    uid: str = Depends(current_user_id),
    limit: int = 50,
):
    row = await load_session_messages(user_id=uid, session_id=session_id, limit=limit)
    if not row:
        raise HTTPException(404, "Session not found")
    return SessionResumeResponse(
        session_id=session_id,
        messages=row["messages"] or [],
        stm_populated=False,
        last_updated=row["updated_at"].isoformat(),
    )


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, uid: str = Depends(current_user_id)):
    """Delete a session. Messages follow via ON DELETE CASCADE.

    Redis STM is deliberately NOT cleared here. This route has to behave the
    same whether it is served by the agent (Redis on localhost) or by the Lambda
    (no Redis at all), and reaching for an unreachable Redis would cost a socket
    timeout on every delete. The orphaned stm:{session_id} key does no damage:
    the memory node reads PostgreSQL only, so nothing on the chat path trusts
    that cache, and the key expires on its own.
    """
    pg = get_pg_client()
    await pg.connect()
    result = await pg.execute(
        "DELETE FROM conversations WHERE user_id = $1::uuid AND session_id = $2::uuid",
        uid, session_id,
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Session not found")
    return {"deleted": session_id, "result": result}


# ── User memory (Tier 1 always-on facts) ──────────────────────────────────────


@router.post("/me/memory", response_model=UserMemoryItem)
async def create_user_memory(body: UserMemoryCreate, uid: str = Depends(current_user_id)):
    """Create a user fact (Tier 1 always-on memory). D14 MVP: user self-reports.

    The two statements run inside one transaction, which matters more than it
    looks. Served through Neon's pooled endpoint, PgBouncer lends a backend per
    transaction — so two separate calls could execute on two different servers,
    with the second seeing a database where the first had not happened. The
    insert into `users` exists precisely to satisfy the foreign key of the
    insert that follows it, so they have to be one unit.

    Something the user types is theirs: source 'user', status active. An
    instruction lands on tier 1, a fact on tier 2 (its vector is filled in by
    a later chat turn).
    """
    pg = get_pg_client()
    memory_type = body.memory_type
    if memory_type is None:
        memory_type = "instruction" if body.kind == "instruction" else "profile"
    kind = "instruction" if memory_type == "instruction" else "fact"

    async with pg.transaction() as conn:
        # Ensure user row exists — the FK on user_memory requires it, and a user
        # authenticated by Cognito may not have been written here yet.
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1::uuid) ON CONFLICT (id) DO NOTHING", uid,
        )
        tier = 2
        if memory_type == "instruction":
            try:
                rows = await conn.fetch(
                    "SELECT project FROM user_memory WHERE user_id = $1::uuid "
                    "AND valid = true AND memory_type = 'area' AND project IS NOT NULL",
                    uid,
                )
            except Exception:
                rows = []
            area_topics = {str(r["project"]).strip().casefold() for r in rows}
            if body.project and body.project.strip().casefold() in area_topics:
                tier = 2
            else:
                tier = 1
        row = await conn.fetchrow(
            """INSERT INTO user_memory (user_id, fact_text, category, kind, tier,
                                          status, project, source, memory_type)
               VALUES ($1::uuid, $2, $3, $4, $5, 'active', $6, 'user', $7)
               RETURNING id, created_at, updated_at, said_at""",
            uid, body.fact_text, body.category, kind, tier, body.project,
            memory_type,
        )

    return UserMemoryItem(
        id=str(row["id"]),
        fact_text=body.fact_text,
        category=body.category,
        kind=kind,
        tier=tier,
        status="active",
        project=body.project,
        valid=True,
        source="user",
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
        said_at=row["said_at"].isoformat() if row["said_at"] else None,
        memory_type=memory_type,
    )


@router.get("/me/memory", response_model=UserMemoryListResponse)
async def list_user_memory(uid: str = Depends(current_user_id)):
    """List user rows, candidates included, newest first."""
    pg = get_pg_client()
    await pg.connect()

    rows = await pg.fetch(
        """SELECT id, fact_text, category, kind, tier, status, project,
                  valid, source, created_at, updated_at, said_at,
                  memory_type
           FROM user_memory
           WHERE user_id = $1::uuid AND valid = true
           ORDER BY created_at DESC, id""",
        uid,
    )
    return UserMemoryListResponse(facts=[
        UserMemoryItem(
            id=str(r["id"]),
            fact_text=r["fact_text"],
            category=r["category"],
            kind=r["kind"],
            tier=r["tier"],
            status=r["status"],
            project=r["project"],
            valid=r["valid"],
            source=r["source"],
            created_at=r["created_at"].isoformat(),
            updated_at=r["updated_at"].isoformat(),
            said_at=r["said_at"].isoformat() if r["said_at"] else None,
            memory_type=(r["memory_type"] if "memory_type" in r else None),
        )
        for r in rows
    ])


@router.post("/me/memory/{fact_id}", response_model=UserMemoryItem)
async def update_user_memory(
    fact_id: UUID, body: UserMemoryUpdate, uid: str = Depends(current_user_id),
):
    """Edit one fact, retaining its identity, category and conversation source.

    POST fits the gateway's existing CORS methods. The row lock and timestamp
    check prevent overwriting a concurrent edit, extraction or deletion.
    """
    pg = get_pg_client()
    async with pg.transaction() as conn:
        previous = await conn.fetchrow(
            """SELECT updated_at, memory_type, kind, tier FROM user_memory
               WHERE id = $1::uuid AND user_id = $2::uuid AND valid = true
               FOR UPDATE""",
            fact_id, uid,
        )
        if previous is None:
            raise HTTPException(404, "Fact not found")
        if previous["updated_at"] != body.expected_updated_at:
            raise HTTPException(409, "Memory changed. Reload before saving.")

        try:
            memory_type = previous["memory_type"]
        except Exception:
            try:
                kind_prev = previous["kind"]
            except Exception:
                kind_prev = "fact"
            memory_type = "instruction" if kind_prev == "instruction" else "profile"
        if body.memory_type is not None:
            memory_type = body.memory_type
        kind = "instruction" if memory_type == "instruction" else "fact"
        try:
            tier = previous["tier"]
        except Exception:
            tier = 1 if memory_type == "instruction" else 2
        if body.memory_type is not None:
            if memory_type != "instruction":
                tier = 2
            else:
                try:
                    rows = await conn.fetch(
                        "SELECT project FROM user_memory WHERE user_id = $1::uuid "
                        "AND valid = true AND memory_type = 'area' AND project IS NOT NULL",
                        uid,
                    )
                except Exception:
                    rows = []
                area_topics = {str(r["project"]).strip().casefold() for r in rows}
                proj = body.project if body.project is not None else previous.get("project") if hasattr(previous, "get") else None
                try:
                    proj = body.project if body.project is not None else previous["project"]
                except Exception:
                    proj = body.project
                if proj and str(proj).strip().casefold() in area_topics:
                    tier = 2
                else:
                    tier = 1

        row = await conn.fetchrow(
            """UPDATE user_memory SET fact_text = $3, memory_type = $4,
                  kind = $5, tier = $6, said_at = now(), updated_at = now()
               WHERE id = $1::uuid AND user_id = $2::uuid AND valid = true
               RETURNING id, fact_text, category, kind, tier, status, project,
                         valid, source, created_at, updated_at, said_at,
                         memory_type""",
            fact_id, uid, body.fact_text, memory_type, kind, tier,
        )
    return UserMemoryItem(
        id=str(row["id"]), fact_text=row["fact_text"], category=row["category"],
        kind=row["kind"], tier=row["tier"], status=row["status"],
        project=row["project"], valid=row["valid"], source=row["source"],
        created_at=row["created_at"].isoformat(), updated_at=row["updated_at"].isoformat(),
        said_at=row["said_at"].isoformat() if row["said_at"] else None,
        memory_type=row.get("memory_type"),
    )


@router.delete("/me/memory")
async def delete_all_user_memory(uid: str = Depends(current_user_id)):
    """Hard-delete every memory row of the caller, typed and extracted alike."""
    pg = get_pg_client()
    await pg.connect()

    result = await pg.execute(
        "DELETE FROM user_memory WHERE user_id = $1::uuid",
        uid,
    )
    try:
        deleted = int(result.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        deleted = 0
    return {"deleted": deleted}


@router.delete("/me/memory/{fact_id}")
async def delete_user_memory(fact_id: str, uid: str = Depends(current_user_id)):
    """Hard-delete a user fact. The user_id predicate is the ownership check."""
    pg = get_pg_client()
    await pg.connect()

    result = await pg.execute(
        """DELETE FROM user_memory
           WHERE id = $1::uuid AND user_id = $2::uuid""",
        fact_id, uid,
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Fact not found")
    return {"deleted": fact_id}
