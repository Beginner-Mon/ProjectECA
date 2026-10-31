---
tags: [plan, auth, quota, admin, backend, frontend, infra]
status: in-progress
date: 2026-10-31
---

# Roles, tiers and daily token quota

**Goal:** limit how many tokens each user can spend per day on ECA's own model,
with tiers an admin manages from the app. Idea taken from
`ProjectECA_auth_files.zip` (written for the deleted `agentic_rag_gemini`
gateway) and rebuilt on our stack: Cognito, LangGraph `/chat`, Neon.

## Decisions

| Question | Decision |
|---|---|
| Roles or a permission system? | Two roles only: `admin` and everyone else. Quota is **data** (tiers), not a permission. Revisit fine-grained permissions only when a third kind of staff appears. |
| Where is the admin role? | Cognito group `admin` (`ECA_UI/frontend/amplify/auth/resource.ts`), read from the ID token's `cognito:groups`. |
| Who makes someone admin? | Only people with AWS access, in the Cognito console/CLI. **No app route can do it**, so a stolen admin login cannot mint more admins. |
| Where are tiers and usage? | Neon: `plans`, `user_plan`, `admin_audit` (migration `018_plans_quota`). |
| When does the day reset? | Local midnight in `QUOTA_RESET_TZ` (default `Asia/Ho_Chi_Minh`). Lazy: a row from another day counts as zero; nothing runs at midnight. |
| What counts? | Real tokens the graph reports, summed over every node of the turn, charged after the turn. Not charged: BYOK turns (unless the user's provider failed and ECA answered), admins (recorded, unlimited). |
| Over the limit? | `POST /chat` answers 429 `{detail: {code: "quota_exhausted", resets_at, …}}` + `Retry-After`, before the graph runs. The chat shows a non-retryable message. |
| Quota table down? | Fails **open** (logs `quota_check_failed`): it limits cost, it is not a security boundary. |
| Default tiers | `free` 20,000 (default) · `pro` 100,000 · `ultra` 500,000 tokens/day. **Owner to confirm numbers** and whether pro/ultra are paid. |

## Defence in depth for admin

1. API Gateway Cognito authorizer — signed-in user.
2. `require_admin` (api/auth.py) — token has the `admin` group.
3. Row-level security (018): `plans` writes, other users' `user_plan` rows and
   `admin_audit` need `app.role = 'admin'`, bound from the verified token by
   `PostgresClient.user_scope`. A trigger stops non-admins changing
   `plan_name` / `limit_override` even on their own row.
4. Every admin write inserts an `admin_audit` row in the same transaction.
   Cognito group changes are in CloudTrail.

## Known limits

- Check-then-charge: parallel turns started on the last tokens can overshoot by
  about one turn each. Accepted — the cost of a turn is unknown until it ends.
- A BYOK turn whose provider fails falls back to ECA even when the user is over
  quota (the check is skipped for BYOK). Small; revisit if abused.
- Group changes reach the app on the next token refresh (≤ 1 h), or at once
  after a Cognito global sign-out.
- `done.total_tokens` (SSE) still reports only the answering node's tokens, not
  the turn's sum — pre-existing, left as is; the quota uses its own sum.

## Rollout (in order)

1. `alembic upgrade head` on Neon (018) — as the owner role, like every migration.
2. Deploy Amplify (`release`) — creates the `admin` Cognito group.
3. Add the first admin: Cognito console → user pool → Groups → `admin` → Add user
   (or `aws cognito-idp admin-add-user-to-group --user-pool-id … --username … --group-name admin`).
4. `cdk deploy` the REST API stack (new `/me/quota`, `/admin/*` routes) and the
   CRUD Lambda (`python infra/build_crud_api.py` first — it now needs `tzdata`).
5. Agent image builds in CI from `release` (also needs `tzdata`).
6. Sign in as the admin, open Profile → Usage → "Manage tiers and quotas".
