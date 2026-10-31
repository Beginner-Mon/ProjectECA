---
tags: [ops, runbook, quota, admin, neon, cognito, cdk]
date: 2026-10-31
status: pending
---

# Rollout: admin role, tiers and daily token quota

> Code: `3276f2a` on `origin/release` (pushed 31/10). Design: [[roles-and-quota]].
> Who: someone with the Neon **owner** DSN and AWS access to account
> `244203483654`, region `us-east-1`.

**Until step 2 is done, nothing breaks:** chat keeps working because the quota
check fails open (logs `quota_check_failed` / `quota_charge_failed`). Only
Profile → Usage and the admin page show errors.

Do the steps **in order**. Each one has a check — do not move on until it passes.

---

## 0. Wait for the release deploys

```bash
gh run list --branch release --limit 4
```

**Check:** `Deploy Agent Image` and `Release Unit Tests` for `feat(quota): …`
are `completed success`, and the Amplify build of `release` is green in the
Amplify console. The agent Lambda is updated by that CI job itself
(`update-function-code`) — no `cdk deploy VvaAgentStack` needed.

---

## 1. Try migration 018 on a Neon branch first

018 has not been run against a real Postgres yet (RLS policies + a plpgsql
trigger). A Neon branch is a free copy of production to test it on.

1. Neon console → project → **Branches → Create branch** from `main`, name
   `quota-018-test`. Copy its **owner** connection string (direct endpoint, not
   `-pooler`, keep the `.c-NN` segment).
2. Run the migration against the branch:

   ```bash
   cd agenticRAG/langgraph_agents
   VVA_PG_DSN_OWNER='postgresql://neondb_owner:<pw>@<branch-host>/neondb?sslmode=require' alembic upgrade head
   VVA_PG_DSN_OWNER='postgresql://neondb_owner:<pw>@<branch-host>/neondb?sslmode=require' alembic current
   ```

   **Check:** `alembic current` prints `018_plans_quota (head)`.

3. In the Neon SQL editor on the branch, connected as **`eca_user`** (the app
   role — the owner bypasses RLS and would prove nothing). Run each block on its own:

   ```sql
   -- Seed tiers are there
   SELECT name, daily_token_limit, is_default FROM plans ORDER BY daily_token_limit;
   -- expect: free 20000 t / pro 100000 f / ultra 500000 f
   ```

   ```sql
   -- A normal user cannot change a tier (expect: UPDATE 0)
   BEGIN;
   SELECT set_config('app.user_id', '00000000-0000-0000-0000-0000000000aa', true),
          set_config('app.role', 'user', true);
   UPDATE plans SET daily_token_limit = 1 WHERE name = 'free';
   ROLLBACK;
   ```

   ```sql
   -- A normal user cannot promote themselves (expect: ERROR only an admin may …)
   BEGIN;
   SELECT set_config('app.user_id', '00000000-0000-0000-0000-0000000000aa', true),
          set_config('app.role', 'user', true);
   INSERT INTO users (id) VALUES ('00000000-0000-0000-0000-0000000000aa') ON CONFLICT DO NOTHING;
   INSERT INTO user_plan (user_id, plan_name) VALUES ('00000000-0000-0000-0000-0000000000aa', 'ultra');
   ROLLBACK;
   ```

   ```sql
   -- An admin can (expect: UPDATE 1, then ROLLBACK leaves nothing behind)
   BEGIN;
   SELECT set_config('app.user_id', '00000000-0000-0000-0000-0000000000aa', true),
          set_config('app.role', 'admin', true);
   UPDATE plans SET daily_token_limit = 25000 WHERE name = 'free';
   ROLLBACK;
   ```

   ```sql
   -- The audit log is closed to normal users (expect: 0 rows, no error)
   BEGIN;
   SELECT set_config('app.user_id', '00000000-0000-0000-0000-0000000000aa', true),
          set_config('app.role', 'user', true);
   SELECT count(*) FROM admin_audit;
   ROLLBACK;
   ```

4. **If anything above differs:** stop, delete the branch, report the output.
   Do not run 018 on `main`.
5. All good → delete the `quota-018-test` branch.

---

## 2. Run migration 018 on production

Same commands, production **owner** DSN (direct endpoint):

```bash
cd agenticRAG/langgraph_agents
VVA_PG_DSN_OWNER='<production owner DSN>' alembic upgrade head
VVA_PG_DSN_OWNER='<production owner DSN>' alembic current
```

**Check:** `018_plans_quota (head)`. Within a minute, CloudWatch logs of the
agent stop showing `quota_check_failed`.

**Undo if needed:** `alembic downgrade 017_memory_types` (drops the three
tables; the code fails open again, chat unaffected).

---

## 3. Confirm the `admin` group exists in Cognito

Amplify's `release` build should have created it in the production pool
`us-east-1_6mSqgs4BA`.

```bash
aws cognito-idp list-groups --user-pool-id us-east-1_6mSqgs4BA --region us-east-1
```

**Check:** a group named `admin`. If it is missing (the production pool is not
managed by that Amplify build), create it:

```bash
aws cognito-idp create-group --user-pool-id us-east-1_6mSqgs4BA --region us-east-1 \
    --group-name admin --description "Manages tiers and token quotas (ECA admin page)"
```

---

## 4. Add the first admin

Find the person's Cognito username (for Google sign-ins it is not the email):

```bash
aws cognito-idp list-users --user-pool-id us-east-1_6mSqgs4BA --region us-east-1 \
    --filter 'email = "owner@example.com"' --query 'Users[].Username'
```

Add them (repeat for every username the list returns, e.g. a Google-linked
account can have one per identity):

```bash
aws cognito-idp admin-add-user-to-group --user-pool-id us-east-1_6mSqgs4BA --region us-east-1 \
    --username '<Username from above>' --group-name admin
```

Or in the console: Cognito → user pool → **Groups → admin → Add user to group**.

**Check:**
```bash
aws cognito-idp list-users-in-group --user-pool-id us-east-1_6mSqgs4BA --region us-east-1 \
    --group-name admin --query 'Users[].Attributes[?Name==`email`].Value'
```

The new admin must **sign out and back in** (or wait up to an hour) so their
token carries the group.

Removing an admin later: `admin-remove-user-from-group`, then
`admin-user-global-sign-out` so it takes effect at once.

---

## 5. Deploy the CRUD Lambda and the API Gateway routes

`/me/quota` and `/admin/*` are served by the CRUD Lambda and must be declared
on the REST API; without this the gateway answers **403** to them.

```bash
git fetch origin && git checkout release && git pull --ff-only
python infra/build_crud_api.py          # rebuilds the zip from this tree (now includes tzdata)
cd infra
cdk diff --method=template VvaCrudApiStack VvaRestApiStack     # read-only
```

**Check the diff:** CRUD = new code asset only; REST API = new resources
`me/quota`, `admin/plans`, `admin/plans/{name}`, `admin/users`,
`admin/users/{user_id}`, `admin/users/{user_id}/reset`, `admin/audit` and their
methods. **Nothing removed or replaced.** If it shows deletions, stop.

```bash
cdk deploy --exclusively VvaCrudApiStack
cdk deploy --exclusively VvaRestApiStack
```

Never `cdk deploy --all` (infra/README.md). Then switch back to your working branch.

**Check (no token — should be 401, not 403):** wait ~1 minute for the stage to
pick up the deployment (it answers 403 for the first ~40 s), then

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<rest-api-id>.execute-api.us-east-1.amazonaws.com/v1/me/quota
curl -s -o /dev/null -w '%{http_code}\n' https://<rest-api-id>.execute-api.us-east-1.amazonaws.com/v1/admin/plans
```

Both `401`. A `403` that does not go away = the route is not deployed.

---

## 6. Smoke test in the app

1. **As a normal user:** Profile → **Usage** shows `Plan: free`, `0 of 20,000`
   (or today's usage) and a reset time at local midnight. Send a message,
   reopen the profile: usage went up.
2. **As the admin:** Profile → Usage shows **Unlimited** and a
   **Manage tiers and quotas** button → `/admin`:
   - Tiers table lists free / pro / ultra.
   - Search the normal user's email → change tier to `pro` → the row shows
     the new limit. **Recent changes** shows `user.update`.
   - Change it back.
3. **Quota block works:** as admin, set the test user's personal limit to `1`.
   As that user, send two messages: the second is refused with "You've used
   today's message allowance…". As admin, clear the personal limit (empty box →
   Save) and **Reset today's usage**.
4. **Admin routes are closed to users:** as the normal user, open `/admin`
   directly → "This page is for administrators."

Log the outcome in `docs/worklogs/DD-MM-YYYY.md`, and set `status: done` on
this file and on `docs/plans/roles-and-quota.md`.

---

## Before or after: decisions for the Owner

- Tier names and limits (seeded: free 20,000 · pro 100,000 · ultra 500,000
  tokens/day). Changeable any time from the admin page — no deploy.
- Whether `pro` / `ultra` are paid plans or only assigned by admins.
- Who is an admin.
