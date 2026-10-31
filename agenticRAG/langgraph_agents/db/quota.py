"""Daily token quota per tier (migration 018, docs/plans/roles-and-quota.md).

The day is a calendar day in QUOTA_RESET_TZ (default Asia/Ho_Chi_Minh): every
user resets together at local midnight, which is easy to show ("resets at
00:00"). Nothing runs at midnight — a row whose `usage_day` is not today simply
counts as zero, and the next charge starts the new day.

Usage is charged AFTER a turn, with the real token count the graph summed
(`total_tokens`), because the cost of a turn is unknown until it ends. The
check before a turn only asks "anything left?". So parallel turns started on
the last few tokens can each finish and overshoot by about one turn — the
price of charging real usage instead of a guess, and bounded.

Admins are unlimited (usage is still recorded). Turns answered by the user's
own model (BYOK) are not charged — see api/main.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from langgraph_agents.shared import get_pg_client

DEFAULT_RESET_TZ = "Asia/Ho_Chi_Minh"


def reset_zone() -> ZoneInfo:
    return ZoneInfo(os.getenv("QUOTA_RESET_TZ", DEFAULT_RESET_TZ))


def quota_day(now: Optional[datetime] = None) -> date:
    """Today's date in the reset time zone — the key usage is counted under."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(reset_zone()).date()


def next_reset(now: Optional[datetime] = None) -> datetime:
    """The next local midnight, in UTC."""
    zone = reset_zone()
    now = now or datetime.now(timezone.utc)
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=zone).astimezone(timezone.utc)


@dataclass(frozen=True)
class QuotaStatus:
    plan: str
    limit: Optional[int]  # None = unlimited (admin)
    used: int
    resets_at: datetime

    @property
    def remaining(self) -> Optional[int]:
        return None if self.limit is None else max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    def to_dict(self) -> dict:
        return {
            "plan": self.plan,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "resets_at": self.resets_at.isoformat(),
        }


def used_today(usage_day: Optional[date], tokens_used: int, today: date) -> int:
    """A row from another day counts as nothing: the lazy midnight reset."""
    return int(tokens_used) if usage_day == today else 0


def effective_limit(plan_limit: int, limit_override: Optional[int], is_admin: bool) -> Optional[int]:
    if is_admin:
        return None
    return int(limit_override) if limit_override is not None else int(plan_limit)


async def get_status(user_id: str, email: Optional[str], is_admin: bool) -> QuotaStatus:
    """The caller's quota, creating their row on the default tier if needed.

    Also refreshes the stored email from the verified token, which is how the
    admin page can search by email without asking Cognito.
    """
    pg = get_pg_client()
    today = quota_day()
    async with pg.transaction() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1::uuid) ON CONFLICT (id) DO NOTHING", user_id,
        )
        await conn.execute(
            """INSERT INTO user_plan (user_id, plan_name, email)
               VALUES ($1::uuid, (SELECT name FROM plans WHERE is_default), $2)
               ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email
               WHERE user_plan.email IS DISTINCT FROM EXCLUDED.email""",
            user_id, email,
        )
        row = await conn.fetchrow(
            """SELECT up.plan_name, up.limit_override, up.usage_day, up.tokens_used,
                      p.daily_token_limit
               FROM user_plan up JOIN plans p ON p.name = up.plan_name
               WHERE up.user_id = $1::uuid""",
            user_id,
        )
    return QuotaStatus(
        plan=row["plan_name"],
        limit=effective_limit(row["daily_token_limit"], row["limit_override"], is_admin),
        used=used_today(row["usage_day"], row["tokens_used"], today),
        resets_at=next_reset(),
    )


async def charge(user_id: str, tokens: int) -> None:
    """Add a finished turn's tokens to today's usage. One atomic statement."""
    if tokens <= 0:
        return
    pg = get_pg_client()
    async with pg.transaction() as conn:
        await conn.execute(
            """UPDATE user_plan
               SET tokens_used = CASE WHEN usage_day = $2 THEN tokens_used + $3 ELSE $3 END,
                   usage_day = $2,
                   updated_at = now()
               WHERE user_id = $1::uuid""",
            user_id, quota_day(), int(tokens),
        )
