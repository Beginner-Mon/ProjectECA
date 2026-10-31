"""Daily token quota: the user's own view, and the admin routes that manage it.

    GET    /me/quota                       the caller's tier, limit, usage, reset time
    GET    /admin/plans                    every tier
    POST   /admin/plans                    add a tier
    POST   /admin/plans/{name}             change a tier's limit, or make it the default
    DELETE /admin/plans/{name}             remove a tier nobody is on
    GET    /admin/users?email=…            find users (by email substring)
    POST   /admin/users/{user_id}          change a user's tier / personal limit
    POST   /admin/users/{user_id}/reset    zero a user's usage for today
    GET    /admin/audit                    the latest admin changes

Who is an admin comes from the verified token's `cognito:groups` (api/auth.py)
and is enforced twice: `require_admin` here, and the row-level policies of
migration 018 in the database. Nothing here can make anyone an admin — that is
done in the Cognito console only (docs/plans/roles-and-quota.md).

Updates are POST, not PATCH: the API Gateway CORS preflight
(infra/infra/rest_api_stack.py) allows only GET, POST and DELETE, so a browser
would never send a PATCH.

Every admin write goes into admin_audit in the same transaction as the change,
so a change without its audit row cannot happen.
"""

from __future__ import annotations

import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from langgraph_agents.api.auth import AuthUser, current_user, require_admin
from langgraph_agents.db import quota
from langgraph_agents.shared import get_pg_client

router = APIRouter(tags=["quota"])

PLAN_NAME_PATTERN = r"^[a-z0-9_-]{1,32}$"
# A ceiling on any single limit, so a typo cannot hand out a billion tokens.
MAX_DAILY_LIMIT = 50_000_000


class PlanCreate(BaseModel):
    name: str = Field(pattern=PLAN_NAME_PATTERN)
    daily_token_limit: int = Field(ge=0, le=MAX_DAILY_LIMIT)


class PlanUpdate(BaseModel):
    daily_token_limit: Optional[int] = Field(default=None, ge=0, le=MAX_DAILY_LIMIT)
    is_default: Optional[bool] = None


class UserPlanUpdate(BaseModel):
    plan_name: Optional[str] = Field(default=None, pattern=PLAN_NAME_PATTERN)
    # Explicit null clears the personal limit; absent leaves it alone.
    limit_override: Optional[int] = Field(default=None, ge=0, le=MAX_DAILY_LIMIT)
    clear_limit_override: bool = False


def _plan_row(r) -> dict:
    return {
        "name": r["name"],
        "daily_token_limit": r["daily_token_limit"],
        "is_default": r["is_default"],
        "users": r["users"],
    }


async def _audit(conn, admin: AuthUser, action: str, target: str, detail: dict) -> None:
    await conn.execute(
        """INSERT INTO admin_audit (actor_id, actor_email, action, target, detail)
           VALUES ($1::uuid, $2, $3, $4, $5::jsonb)""",
        admin.id, admin.email, action, target, json.dumps(detail),
    )


# ── The caller's own quota ────────────────────────────────────────────────────


@router.get("/me/quota")
async def my_quota(user: AuthUser = Depends(current_user)):
    status = await quota.get_status(user.id, user.email, user.is_admin)
    return {**status.to_dict(), "is_admin": user.is_admin}


# ── Plans (tiers) ─────────────────────────────────────────────────────────────

_PLANS_SQL = """SELECT p.name, p.daily_token_limit, p.is_default,
                       (SELECT count(*) FROM user_plan up WHERE up.plan_name = p.name) AS users
                FROM plans p"""


@router.get("/admin/plans")
async def list_plans(admin: AuthUser = Depends(require_admin)):
    pg = get_pg_client()
    async with pg.transaction() as conn:
        rows = await conn.fetch(_PLANS_SQL + " ORDER BY p.daily_token_limit, p.name")
    return {"plans": [_plan_row(r) for r in rows]}


@router.post("/admin/plans", status_code=201)
async def create_plan(body: PlanCreate, admin: AuthUser = Depends(require_admin)):
    pg = get_pg_client()
    async with pg.transaction() as conn:
        created = await conn.fetchval(
            """INSERT INTO plans (name, daily_token_limit) VALUES ($1, $2)
               ON CONFLICT (name) DO NOTHING RETURNING name""",
            body.name, body.daily_token_limit,
        )
        if created is None:
            raise HTTPException(409, "a tier with that name already exists")
        await _audit(conn, admin, "plan.create", body.name,
                     {"daily_token_limit": body.daily_token_limit})
        row = await conn.fetchrow(_PLANS_SQL + " WHERE p.name = $1", body.name)
    return _plan_row(row)


@router.post("/admin/plans/{name}")
async def update_plan(name: str, body: PlanUpdate, admin: AuthUser = Depends(require_admin)):
    if body.is_default is False:
        # There must always be a default; make another tier the default instead.
        raise HTTPException(422, "set is_default=true on another tier instead")
    pg = get_pg_client()
    async with pg.transaction() as conn:
        before = await conn.fetchrow(_PLANS_SQL + " WHERE p.name = $1", name)
        if before is None:
            raise HTTPException(404, "no such tier")
        if body.daily_token_limit is not None:
            await conn.execute(
                "UPDATE plans SET daily_token_limit = $2, updated_at = now() WHERE name = $1",
                name, body.daily_token_limit,
            )
        if body.is_default:
            # Two statements, one transaction: the unique index allows one default.
            await conn.execute("UPDATE plans SET is_default = false WHERE is_default AND name <> $1", name)
            await conn.execute("UPDATE plans SET is_default = true, updated_at = now() WHERE name = $1", name)
        await _audit(conn, admin, "plan.update", name, {
            "before": {"daily_token_limit": before["daily_token_limit"], "is_default": before["is_default"]},
            "after": body.model_dump(exclude_none=True),
        })
        row = await conn.fetchrow(_PLANS_SQL + " WHERE p.name = $1", name)
    return _plan_row(row)


@router.delete("/admin/plans/{name}")
async def delete_plan(name: str, admin: AuthUser = Depends(require_admin)):
    pg = get_pg_client()
    async with pg.transaction() as conn:
        row = await conn.fetchrow(_PLANS_SQL + " WHERE p.name = $1", name)
        if row is None:
            raise HTTPException(404, "no such tier")
        if row["is_default"]:
            raise HTTPException(409, "the default tier cannot be deleted")
        if row["users"]:
            raise HTTPException(409, f"{row['users']} user(s) are on this tier; move them first")
        await conn.execute("DELETE FROM plans WHERE name = $1", name)
        await _audit(conn, admin, "plan.delete", name, {"daily_token_limit": row["daily_token_limit"]})
    return {"deleted": name}


# ── Users ─────────────────────────────────────────────────────────────────────

_USERS_SQL = """SELECT up.user_id, up.email, up.plan_name, up.limit_override,
                       up.usage_day, up.tokens_used, p.daily_token_limit
                FROM user_plan up JOIN plans p ON p.name = up.plan_name"""


def _user_row(r) -> dict:
    today = quota.quota_day()
    limit = quota.effective_limit(r["daily_token_limit"], r["limit_override"], is_admin=False)
    return {
        "user_id": str(r["user_id"]),
        "email": r["email"],
        "plan": r["plan_name"],
        "limit_override": r["limit_override"],
        "limit": limit,
        "used_today": quota.used_today(r["usage_day"], r["tokens_used"], today),
    }


@router.get("/admin/users")
async def find_users(
    email: str = Query(default="", max_length=254),
    limit: int = Query(default=50, ge=1, le=200),
    admin: AuthUser = Depends(require_admin),
):
    pg = get_pg_client()
    pattern = "%" + email.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    async with pg.transaction() as conn:
        rows = await conn.fetch(
            _USERS_SQL + " WHERE lower(coalesce(up.email, '')) LIKE $1 "
            "ORDER BY up.updated_at DESC LIMIT $2",
            pattern, limit,
        )
    return {"users": [_user_row(r) for r in rows], "resets_at": quota.next_reset().isoformat()}


@router.post("/admin/users/{user_id}")
async def update_user_plan(user_id: UUID, body: UserPlanUpdate, admin: AuthUser = Depends(require_admin)):
    user_id = str(user_id)
    pg = get_pg_client()
    async with pg.transaction() as conn:
        before = await conn.fetchrow(_USERS_SQL + " WHERE up.user_id = $1::uuid", user_id)
        if before is None:
            raise HTTPException(404, "no quota row for that user (they have not chatted yet)")
        if body.plan_name is not None:
            exists = await conn.fetchval("SELECT 1 FROM plans WHERE name = $1", body.plan_name)
            if not exists:
                raise HTTPException(422, "no such tier")
            await conn.execute(
                "UPDATE user_plan SET plan_name = $2, updated_at = now() WHERE user_id = $1::uuid",
                user_id, body.plan_name,
            )
        if body.clear_limit_override:
            await conn.execute(
                "UPDATE user_plan SET limit_override = NULL, updated_at = now() WHERE user_id = $1::uuid",
                user_id,
            )
        elif body.limit_override is not None:
            await conn.execute(
                "UPDATE user_plan SET limit_override = $2, updated_at = now() WHERE user_id = $1::uuid",
                user_id, body.limit_override,
            )
        await _audit(conn, admin, "user.update", user_id, {
            "email": before["email"],
            "before": {"plan": before["plan_name"], "limit_override": before["limit_override"]},
            "after": body.model_dump(exclude_none=True, exclude_defaults=True),
        })
        row = await conn.fetchrow(_USERS_SQL + " WHERE up.user_id = $1::uuid", user_id)
    return _user_row(row)


@router.post("/admin/users/{user_id}/reset")
async def reset_user_usage(user_id: UUID, admin: AuthUser = Depends(require_admin)):
    user_id = str(user_id)
    pg = get_pg_client()
    async with pg.transaction() as conn:
        before = await conn.fetchrow(_USERS_SQL + " WHERE up.user_id = $1::uuid", user_id)
        if before is None:
            raise HTTPException(404, "no quota row for that user")
        await conn.execute(
            "UPDATE user_plan SET tokens_used = 0, updated_at = now() WHERE user_id = $1::uuid", user_id,
        )
        await _audit(conn, admin, "user.reset_usage", user_id, {
            "email": before["email"],
            "used_today": quota.used_today(before["usage_day"], before["tokens_used"], quota.quota_day()),
        })
        row = await conn.fetchrow(_USERS_SQL + " WHERE up.user_id = $1::uuid", user_id)
    return _user_row(row)


# ── Audit ─────────────────────────────────────────────────────────────────────


@router.get("/admin/audit")
async def audit_log(limit: int = Query(default=100, ge=1, le=500), admin: AuthUser = Depends(require_admin)):
    pg = get_pg_client()
    async with pg.transaction() as conn:
        rows = await conn.fetch(
            "SELECT at, actor_email, action, target, detail FROM admin_audit ORDER BY at DESC LIMIT $1",
            limit,
        )
    return {"entries": [
        {
            "at": r["at"].isoformat(),
            "actor": r["actor_email"],
            "action": r["action"],
            "target": r["target"],
            "detail": json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"],
        }
        for r in rows
    ]}
