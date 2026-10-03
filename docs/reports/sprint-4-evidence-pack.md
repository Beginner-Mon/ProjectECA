---
tags: [report, sprint-4, evidence]
---
# Sprint 4 — Evidence pack

## 1. How to use this pack

Every member writes their own "Contribution Details" section of the Sprint 4 report. This pack is raw material only; nothing in it was written on anyone's behalf.
The git author is the account that made the commit. It is not always the only person who worked on the change, so each member should keep only what they personally did.
One member commits under two git author names, "Tony Lee" and "Le Hac Du". They are the same person and are merged under "Tony Lee" everywhere in this pack.

## 2. How the numbers were counted

- Run date: 2026-10-03. Reporting window: 2026-04-25 to 2026-10-03.
- `origin/release` head before `git fetch origin`: `53ca80cb 2026-09-30 Merge origin/release into feature/langgraph-rewrite`
- `origin/release` head after `git fetch origin`: `53ca80cb 2026-09-30 Merge origin/release into feature/langgraph-rewrite` (unchanged; the fetch only moved `feature/langgraph-rewrite` and `local-testing-improve` on origin)
- De-duplication: most commits exist twice under two hashes on two histories, so `git log --all` double-counts. Commits are de-duplicated on author|date|subject, merges are excluded, and "Le Hac Du" is merged into "Tony Lee". Where two hashes exist, the one reachable from `origin/release` is shown; if neither is, either is shown with "no" in the "On release" column.
- Side effect: two different commits by the same author with the same subject on the same day count once.

Commands run:

```
git log -1 --date=short --format="%h %ad %s" origin/release
git fetch origin
git log --all --since=2026-04-25 --no-merges --date=short --pretty="%an|%ad|%s" | sort -u
git log --all --since=2026-04-25 --no-merges --date=short --pretty="%H|%an|%ad|%s"      # hashes for twin matching
git rev-list origin/release                                                              # membership test for "On release"
git diff-tree --no-commit-id --name-only -r -m --root <hash>                             # files per commit (areas and path groups)
git log --all --since=2026-04-25 --diff-filter=A --author=<name> --name-only --format="@@%ad" --date=short -- docs
```

Path groups: a commit is counted in a group if it touches at least one file under that top-level folder. `text-to-motion` and `infra` are combined in one column. Area in the commit tables is the first two path levels of the files touched.


Total de-duplicated non-merge commits: 489

#### a) per author (whole repo)

| Author | Commits |
|---|---|
| Tri Tran | 406 |
| Nguyen | 66 |
| Tony Lee | 17 |

#### b) per month (whole repo)

| Month | Commits |
|---|---|
| 2026-05 | 12 |
| 2026-06 | 50 |
| 2026-07 | 48 |
| 2026-08 | 181 |
| 2026-09 | 161 |
| 2026-10 | 37 |

#### c) per author and path group

A commit counts in every group it touches, so rows do not sum to the author total.

| Author | ECA_UI | agenticRAG | text-to-motion + infra | docs |
|---|---|---|---|---|
| Nguyen | 34 | 25 | 2 | 45 |
| Tri Tran | 168 | 100 | 98 | 114 |
| Tony Lee | 15 | 6 | 1 | 11 |

## 3. Nguyen

### 3.1 Commits (66 rows)

| Date | Hash | Subject | Area | On release |
|---|---|---|---|---|
| 2026-05-14 | `50065feb` | WIP: Phase 3-4 Double-RAG + Axios + new modules | ECA_UI/api.js, ECA_UI/index.html, README_DEV.md, SpeechLLm/src +15 | yes |
| 2026-05-20 | `0a5e711b` | Switch to model VieNeu | .claude/settings.local.json, SpeechLLm/api_server.py, SpeechLLm/configs, SpeechLLm/requirements.txt +1 | yes |
| 2026-05-20 | `dae84863` | Small claude update | .claude/CLAUDE.md, .claude/settings.local.json, agenticRAG/agentic_rag_gemini, docs/worklogs | yes |
| 2026-05-23 | `22934306` | cleanup: remove pre-v2.4 components, archive old plan docs | .gitignore, PHASE-0.md, PHASE-1.md, PHASE-2.md +6 | yes |
| 2026-05-23 | `492d8a4e` | Phase 2.5: refactor to LangGraph v2.4 architecture | .claude/CLAUDE.md, .gitignore, PHASE-0.md, PHASE-1.md +17 | yes |
| 2026-05-24 | `66137e80` | @ feat(phase-3): MCP servers + FastAPI /chat + BackgroundTasks TTS (v2.4.1) | PHASE-3.5.md, PHASE-3.md, PLAN-v2.4-DRAFT.md, agenticRAG/agentic_rag_gemini +5 | yes |
| 2026-05-24 | `858e8a8b` | fix(mcp): subprocess uses sys.executable + graceful fallback (C1) | agenticRAG/agentic_rag_gemini, docs/worklogs, tests/langgraph_agents | yes |
| 2026-05-26 | `e97d6026` | demo part 1 | .claude/settings.local.json, ARCHITECTURE-DECISION-RESPONSE-NODES.md, ARCHITECTURE-FULL-FLOW-PREDEPLOY.md, ECA_UI/api.js +18 | yes |
| 2026-06-12 | `47c67d0f` | demo part 2 | .claude/CLAUDE.md, .claude/settings.local.json, ECA_UI/api.js, ECA_UI/index.html +16 | yes |
| 2026-06-12 | `bd4930b1` | docs: update run commands to new package path + worklog merge note (K) | README.md, STATUS.md, docs/RUNBOOK.md, docs/worklogs | yes |
| 2026-06-13 | `c2f3935c` | fix(test-ui): done-label uses required_outputs not stale intent + persona cache test assert | ECA_UI/test-ui, TECH_DEBT.md, tests/langgraph_agents | yes |
| 2026-06-13 | `e720a291` | feat(m9): closeout cụm A — clarify động, GDPR wiring, user_memory, security patch | FIX-M9-CLOSEOUT.md, FIX-R1-GDPR-RESUMMARIZE.md, TECH_DEBT.md, agenticRAG/langgraph_agents +2 | yes |
| 2026-06-18 | `25831133` | implement youtube script ingestion | .claude/settings.json, .claude/settings.local.json, FIX-YOUTUBE-PASTE.md, TECH_DEBT.md +4 | yes |
| 2026-07-02 | `6965582d` | successfully integrate BE and FE. | .claude/settings.json, ECA_UI/frontend, ECA_UI/test-ui, FIX-RETRIEVAL-PERF-P123.md +5 | yes |
| 2026-07-02 | `b857c36e` | feat: auth integration + memory fixes + frontend wire + regression tests | .gitignore, ECA_UI/frontend, FIX-AUTH-INTEGRATION.md, FIX-CHATPANEL-WIRE.md +6 | yes |
| 2026-07-21 | `621fcd52` | local_version_finalize | ECA_UI/frontend, FIX-AUTH-INTEGRATION.md, FIX-CHATPANEL-WIRE.md, FIX-RETRIEVAL-PERF-P123.md +6 | yes |
| 2026-07-21 | `878c6ed4` | status_update | docs/tracking | yes |
| 2026-07-23 | `05687f53` | SSE fix and phase A animation | ECA_UI/frontend, agenticRAG/langgraph_agents, docs/plans, docs/tracking +1 | yes |
| 2026-07-23 | `35985d5d` | fix bugs in UI streaming | ECA_UI/frontend, agenticRAG/langgraph_agents, docs/tracking, docs/worklogs +1 | yes |
| 2026-07-24 | `fbe74d4b` | animation fixed | ECA_UI/frontend, README.md, docs/architecture, docs/tracking +1 | yes |
| 2026-07-25 | `5f357870` | fix head controller | ECA_UI/frontend, docs/worklogs | yes |
| 2026-07-25 | `c08b9b8c` | change default pose | ECA_UI/frontend, docs/worklogs | yes |
| 2026-07-30 | `275279dc` | fix shadow? | ECA_UI/frontend, docs/tracking, docs/worklogs | yes |
| 2026-07-30 | `98a94ef1` | Add KB ingest script for pgvector and refactor FSM implementation | .claude/plans, ECA_UI/frontend, agenticRAG/langgraph_agents, docs/tracking +2 | yes |
| 2026-07-30 | `f3d06303` | fix black screen | ECA_UI/frontend, docs/tracking, docs/worklogs | yes |
| 2026-07-31 | `fc3dd328` | Implement ground clamp to prevent character sinking through the floor | ECA_UI/frontend, README.md, docs/tracking, docs/worklogs +2 | yes |
| 2026-08-05 | `0d57c4eb` | Fix 2 problems (check tech docs/tracking/tech-debt.md) | ECA_UI/frontend, docs/mobile-app-links.md, docs/tracking | yes |
| 2026-08-05 | `96c671ae` | refactor authentication flow to eliminate duplicate accounts and enhance user linking | ECA_UI/frontend, docs/tracking, docs/worklogs | yes |
| 2026-08-05 | `9fcae539` | Add worklog for Neon migration and merge conflict resolution | ECA_UI/frontend, agenticRAG/langgraph_agents, config/langgraph.yaml, docs/tracking +3 | yes |
| 2026-08-05 | `d7851fb7` | Neon Migration Plan and Initial Setup | .claude/plans, agenticRAG/langgraph_agents, docs/tracking, scripts/QUICKSTART.md | yes |
| 2026-08-08 | `50eec485` | Update status and roadmap documentation with recent changes and fixes | ECA_UI/frontend, agenticRAG/langgraph_agents, docker-compose.langgraph.yml, docs/auth-google-incident.md +4 | yes |
| 2026-08-08 | `6271376b` | Implement Google account linking without hosted UI | ECA_UI/frontend, docs/auth-google-incident.md, docs/fixes, docs/tracking +1 | yes |
| 2026-08-09 | `b332aafe` | restore Google account chooser prompt after logout to prevent account selection issues | ECA_UI/frontend, docs/fixes, docs/tracking, docs/worklogs | yes |
| 2026-08-10 | `0064021c` | feat: import lazily-used packages at start-up, and say which interpreter | agenticRAG/langgraph_agents, docs/ops, docs/tracking, docs/worklogs +2 | yes |
| 2026-08-10 | `896130c2` | docs: record the decoupling smoke test | docs/worklogs | yes |
| 2026-08-10 | `af88ed8e` | refactor: decouple the LangGraph service from agentic_rag_gemini | agenticRAG/.env.example, agenticRAG/langgraph_agents, data/knowledge_base, docs/ops +8 | yes |
| 2026-08-10 | `bcea7ea0` | chore: fold SEARXNG_SECRET_KEY into agenticRAG/.env, drop the root .env | agenticRAG/.env.example, docker-compose.langgraph.yml, docs/phases | yes |
| 2026-08-10 | `e00d60c7` | chore: delete agentic_rag_gemini | .github/workflows, .gitignore, agenticRAG/agentic_rag_gemini, agenticRAG/langgraph_agents +8 | yes |
| 2026-08-15 | `153c1061` | fix: secrets probe reported AccessDenied as 'no secrets exist' | scripts/amplify_recover.sh | yes |
| 2026-08-15 | `30de050b` | feat: SPA rewrite rule, and say what CORS compared against | ECA_UI/frontend, scripts/amplify_recover.sh | yes |
| 2026-08-15 | `4aabe3e9` | fix: merge left a call to a function it had deleted | ECA_UI/frontend | yes |
| 2026-08-15 | `4c539c13` | feat: make the documented VITE_* Cognito variables actually configure Amplify | ECA_UI/frontend, scripts/QUICKSTART.md | yes |
| 2026-08-15 | `5071a530` | feat: CLI recovery for the stuck Amplify branch deployment | scripts/amplify_recover.sh | yes |
| 2026-08-15 | `51e80ba0` | chore: log the resolved CORS and OAuth origin lists at synth time | ECA_UI/frontend | yes |
| 2026-08-15 | `5f556cad` | fix: deploy attaches to the build a push already started | scripts/amplify_recover.sh | yes |
| 2026-08-15 | `6a9cc867` | fix: schema loop wrote two attributes that do not exist | ECA_UI/frontend, scripts/amplify_recover.sh | yes |
| 2026-08-15 | `ae1adf13` | fix: missing VITE_API_BASE_URL warns instead of killing the app | ECA_UI/frontend, scripts/amplify_recover.sh | yes |
| 2026-08-15 | `b751c084` | fix: scope DynamoDB table names per environment | ECA_UI/frontend | yes |
| 2026-08-15 | `d6893571` | fix: derive the Cognito hosted-UI domain per environment | ECA_UI/frontend | yes |
| 2026-08-15 | `da48dfa5` | fix: force LF on shell scripts | .gitattributes | yes |
| 2026-08-15 | `f55b73ad` | docs: IAM policies and handover steps for the Amplify recovery script | docs/ops | yes |
| 2026-08-15 | `fac0aa49` | fix: read failure events from a stack that has already rolled back | scripts/amplify_recover.sh | yes |
| 2026-08-16 | `5e34890f` | fix: Clerk userId is string\|null\|undefined, the bridge takes string\|null | ECA_UI/frontend | yes |
| 2026-08-16 | `725ebbfc` | merge env and clean repo | ECA_UI/frontend | yes |
| 2026-08-17 | `2991b4a6` | fix: 12 unit tests failing after the Neon/billing merges | tests/langgraph_agents | yes |
| 2026-08-17 | `81cd7d0e` | docs: knowledge base section, and the ingest will take ~30 minutes | docs/ops | yes |
| 2026-08-17 | `91c1e3ae` | docs: Neon report reflects the decision to go ahead | docs/ops | yes |
| 2026-08-17 | `cfa79405` | docs: report on moving Neon to us-east-1 | docs/ops | yes |
| 2026-08-17 | `df4d1c1f` | docs: close the gaps that would have sent Tri back with questions | docs/ops | yes |
| 2026-08-19 | `73219019` | Implement voice cloning and language detection improvements | SpeechLLm/.gitignore, SpeechLLm/src, SpeechLLm/voices, agenticRAG/langgraph_agents +5 | yes |
| 2026-08-19 | `c7db3ee2` | docs: worklog 17/08 and status, before compaction | docs/tracking, docs/worklogs | yes |
| 2026-09-22 | `6d447e8a` | update README | README.md | yes |
| 2026-09-22 | `89f99dd5` | fix(chat): tell the user about the 4,000-character limit before the server does | ECA_UI/frontend | yes |
| 2026-09-22 | `f0d8c4fa` | docs(readme): correct the roadmap and the stack rows against release | README.md | yes |
| 2026-09-24 | `1784c7ea` | document update | docs/ops, docs/tracking, docs/worklogs | yes |
| 2026-10-03 | `7867302b` | Add grading evaluation scripts and NHS ingestion functionality | ECA_UI/frontend, agenticRAG/langgraph_agents, data/knowledge_base, docs/worklogs +3 | no |

### 3.2 Documents first added (71 paths)

| Date | Path |
|---|---|
| 2026-05-14 | `docs/.obsidian/app.json` |
| 2026-05-14 | `docs/.obsidian/appearance.json` |
| 2026-05-14 | `docs/.obsidian/core-plugins.json` |
| 2026-05-14 | `docs/.obsidian/graph.json` |
| 2026-05-14 | `docs/.obsidian/workspace.json` |
| 2026-05-14 | `docs/ARCHITECTURE_REVIEW.md` |
| 2026-05-14 | `docs/agentic_rag_internals.md` |
| 2026-05-14 | `docs/agentic_rag_refactor.md` |
| 2026-05-14 | `docs/agents_catalog.md` |
| 2026-05-14 | `docs/api_contract.md` |
| 2026-05-14 | `docs/dart_architecture.md` |
| 2026-05-14 | `docs/index.md` |
| 2026-05-14 | `docs/setup_guide.md` |
| 2026-05-14 | `docs/speechllm_overview.md` |
| 2026-05-14 | `docs/system_overview.md` |
| 2026-05-14 | `docs/troubleshooting.md` |
| 2026-05-20 | `docs/worklogs/14-05-2026.md` |
| 2026-05-20 | `docs/worklogs/19-05-2026.md` |
| 2026-05-20 | `docs/worklogs/20-05-2026.md` |
| 2026-05-23 | `docs/archive/PHASE-0.md` |
| 2026-05-23 | `docs/archive/PHASE-1.md` |
| 2026-05-23 | `docs/archive/PHASE-2.md` |
| 2026-05-23 | `docs/archive/PHASE-3.md` |
| 2026-05-23 | `docs/archive/PHASE-4.md` |
| 2026-05-23 | `docs/archive/tris_plan.md` |
| 2026-05-23 | `docs/worklogs/21-05-2026-phase2.md` |
| 2026-05-23 | `docs/worklogs/21-05-2026.md` |
| 2026-05-23 | `docs/worklogs/23-05-2026.md` |
| 2026-05-24 | `docs/worklogs/24-05-2026.md` |
| 2026-05-26 | `docs/DEPLOYMENT.md` |
| 2026-05-26 | `docs/RUNBOOK.md` |
| 2026-05-26 | `docs/worklogs/26-05-2026.md` |
| 2026-06-12 | `docs/worklogs/05-06-2026.md` |
| 2026-06-12 | `docs/worklogs/06-06-2026.md` |
| 2026-06-12 | `docs/worklogs/11-06-2026.md` |
| 2026-06-12 | `docs/worklogs/12-06-2026.md` |
| 2026-06-12 | `docs/worklogs/27-05-2026.md` |
| 2026-06-12 | `docs/worklogs/28-05-2026.md` |
| 2026-06-13 | `docs/worklogs/12-06-2026-cont.md` |
| 2026-06-13 | `docs/worklogs/13-06-2026.md` |
| 2026-07-02 | `docs/worklogs/02-07-2026.md` |
| 2026-07-02 | `docs/worklogs/18-06-2026.md` |
| 2026-07-21 | `docs/fixes/auth-integration.md` |
| 2026-07-21 | `docs/fixes/chatpanel-wire.md` |
| 2026-07-21 | `docs/fixes/latency-optimization-1234.md` |
| 2026-07-21 | `docs/fixes/retrieval-perf-p123.md` |
| 2026-07-21 | `docs/worklogs/06-07-2026.md` |
| 2026-07-23 | `docs/plans/facial-animation-plan.md` |
| 2026-07-23 | `docs/worklogs/21-07-2026.md` |
| 2026-07-23 | `docs/worklogs/22-07-2026.md` |
| 2026-07-23 | `docs/worklogs/23-07-2026-avatar.md` |
| 2026-07-23 | `docs/worklogs/23-07-2026.md` |
| 2026-07-25 | `docs/worklogs/25-07-2026.md` |
| 2026-07-30 | `docs/worklogs/30-07-2026.md` |
| 2026-08-05 | `docs/mobile-app-links.md` |
| 2026-08-05 | `docs/worklogs/05-08-2026.md` |
| 2026-08-08 | `docs/auth-google-incident.md` |
| 2026-08-08 | `docs/fixes/google-link-creates-wrong-account.md` |
| 2026-08-08 | `docs/worklogs/08-08-2026.md` |
| 2026-08-09 | `docs/worklogs/09-08-2026.md` |
| 2026-08-10 | `docs/worklogs/10-08-2026.md` |
| 2026-08-15 | `docs/ops/aws-credentials-for-recovery.md` |
| 2026-08-15 | `docs/ops/iam-vva-recover-fix.json` |
| 2026-08-15 | `docs/ops/iam-vva-recover-readonly.json` |
| 2026-08-17 | `docs/ops/neon-migration-us-east-1.md` |
| 2026-08-19 | `docs/worklogs/17-08-2026.md` |
| 2026-08-19 | `docs/worklogs/19-08-2026.md` |
| 2026-09-24 | `docs/worklogs/22-09-2026.md` |
| 2026-10-03 | `docs/worklogs/01-10-2026.md` |
| 2026-10-03 | `docs/worklogs/03-10-2026.md` |
| 2026-10-03 | `docs/worklogs/29-09-2026.md` |

### 3.3 Fill-in template

Summary (3–5 sentences): [to be written by Nguyen]

| Epic / area | What I delivered | Evidence (commit, test, figure no.) |
|---|---|---|
|  |  |  |
|  |  |  |
|  |  |  |

## 4. Tony Lee (git authors "Tony Lee" and "Le Hac Du")

### 4.1 Commits (17 rows)

| Date | Hash | Subject | Area | On release |
|---|---|---|---|---|
| 2026-06-05 | `14bc5fb8` | 3d completed (camera issue persist) | ECA_UI/frontend, scripts/smplx_to_bvh.py | yes |
| 2026-06-05 | `5bc6542b` | ground meshes | ECA_UI/frontend | yes |
| 2026-06-08 | `27bdda83` | camera angle still require fixing | ECA_UI/frontend | yes |
| 2026-06-09 | `5fa1cd6f` | camera angle fixed, needs to change targetting and rotate angle | ECA_UI/frontend | yes |
| 2026-08-15 | `ea574803` | payment | ECA_UI/frontend, agenticRAG/.env.example, agenticRAG/langgraph_agents, infra/sql +2 | yes |
| 2026-08-17 | `3036b67c` | bill | agenticRAG/langgraph_agents | yes |
| 2026-09-19 | `bf9bb0d2` | animation lock temp | .claude/launch.json, ECA_UI/frontend, docs/worklogs, start_services.py | yes |
| 2026-09-24 | `23e5e59a` | clothing physcis | ECA_UI/frontend, agenticRAG/langgraph_agents, docs/worklogs, tests/langgraph_agents | yes |
| 2026-09-25 | `59335e49` | shadow and camera fix | ECA_UI/frontend, docs/worklogs | yes |
| 2026-09-26 | `c89f9295` | background added | ECA_UI/frontend, docs/worklogs | yes |
| 2026-09-26 | `e4b17aad` | background and animation fix | ECA_UI/frontend, docs/worklogs | yes |
| 2026-09-26 | `f2b21a7f` | animation added | ECA_UI/frontend, agenticRAG/langgraph_agents, docs/worklogs, tests/langgraph_agents | yes |
| 2026-09-28 | `ce1948b2` | donation and camera zoom fix | ECA_UI/frontend, agenticRAG/langgraph_agents, docs/worklogs, tests/langgraph_agents | yes |
| 2026-09-29 | `2b3ea54a` | push | ECA_UI/frontend, docs/worklogs | yes |
| 2026-10-03 | `551ced19` | fix conversation error | agenticRAG/langgraph_agents, docs/worklogs, tests/langgraph_agents | no |
| 2026-10-03 | `9798a4f6` | changes to mobile layout | ECA_UI/frontend, docs/worklogs | no |
| 2026-10-03 | `cda73fef` | limit panning | ECA_UI/frontend, docs/worklogs | no |

### 4.2 Documents first added (8 paths)

| Date | Path |
|---|---|
| 2026-09-19 | `docs/worklogs/19-09-2026.md` |
| 2026-09-24 | `docs/worklogs/24-09-2026.md` |
| 2026-09-25 | `docs/worklogs/25-09-2026.md` |
| 2026-09-26 | `docs/worklogs/26-09-2026.md` |
| 2026-09-28 | `docs/worklogs/28-09-2026.md` |
| 2026-09-29 | `docs/worklogs/29-09-2026.md` |
| 2026-10-03 | `docs/worklogs/03-10-2026.md` |
| 2026-10-03 | `docs/worklogs/30-09-2026.md` |

### 4.3 Fill-in template

Summary (3–5 sentences): [to be written by Tony Lee]

| Epic / area | What I delivered | Evidence (commit, test, figure no.) |
|---|---|---|
|  |  |  |
|  |  |  |
|  |  |  |

## 5. Figure list

| Figure | What it shows | Suggested capturer | Used in report section | Captured? |
|---|---|---|---|---|
| 1 | GitHub commit history of the `release` branch | Tri | 6 | ☐ |
| 2 | GitHub Insights → Commit activity for the reporting window | Tri | 6 | ☐ |
| 3 | `docs/worklogs/` folder on GitHub (dated engineering logs) | Tri | 6 | ☐ |
| 4 | Jira board, current state | Team | 6, 8 | ☐ |
| 5 | Login page: email sign-in and Google sign-in | Tri | 6 (AUTH) | ☐ |
| 6 | Export of `docs/architecture/cognito-auth-sequence.drawio` | Tri | 6 (AUTH) | ☐ |
| 7 | Output of the row-level-security isolation test | Tri | 6 (AUTH), 8 | ☐ |
| 8 | Chat with a streamed answer, sources and thumbs up/down | Tri | 6 (FE) | ☐ |
| 9 | Mobile chat dock at phone width | Tri | 6 (FE) | ☐ |
| 10 | Language switch (EN/VI) and avatar picker | Tri | 6 (FE) | ☐ |
| 11 | Avatar playing a generated exercise motion | Team | 6 (KIM) | ☐ |
| 12 | Export of `docs/architecture/aws-topology.drawio` | Tri | 6 (KIM, PLAT) | ☐ |
| 13 | LangGraph flow diagram from `docs/architecture/langgraph-flow-persona.md` | Team | 6 (LG) | ☐ |
| 14 | Context probe V7 result table (`docs/tracking/context-probe-V7.md`) | Tri | 6 (LG) | ☐ |
| 15 | Backend test run summary (pytest) | Tri | 6 | ☐ |
| 16 | Frontend test run summary (npm test) | Tri | 6 | ☐ |
| 17 | Amplify console: successful build of `release`, with the live URL | Tri | 6 (PLAT) | ☐ |
| 18 | ASVS 5.0 checklist summary (branch `owasp-check`) | Tri | 6 (AUTH), 8 | ☐ |

The GitHub Contributors graph is deliberately not in this list; the team decides whether to include it.
