-- ============================================================================
-- Message feedback review queries
--
-- What this is: three read-only queries for reviewing users' 👍/👎 feedback
-- on assistant answers. Paste each one into the Neon console's SQL editor
-- and run it there — there is no admin UI for this yet (plan §4.C).
--
-- Runs as the owner role, which is NOT subject to RLS (007_rls.py does not
-- FORCE row security on message_feedback or messages), so these queries see
-- every user's rows, not just one.
--
-- WARNING: results contain users' health-related questions and comments.
-- View them in the console only. Do not export, paste into tickets/chat, or
-- commit results anywhere.
--
-- How to change the time window: each query has one `interval '7 days'`
-- literal marked with a `-- window` comment. Edit that literal only.
--
-- Which window column: message_feedback.updated_at, not created_at or
-- messages.created_at. A vote that changed today (e.g. 👎 -> 👍, or a 👎
-- edited with a new reason) should count as today's activity, and
-- updated_at is what the upsert in the feedback route bumps on every change.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Query 1: How does the 👎 rate break down by persona over the last 7 days?
-- Grand total row ("ALL") sorts last.
-- ----------------------------------------------------------------------------
WITH stats AS (
    SELECT
        COALESCE(m.extras -> 'meta' ->> 'persona_id', 'unknown')            AS persona,
        COUNT(*) FILTER (WHERE f.rating = 1)                                AS thumbs_up,
        COUNT(*) FILTER (WHERE f.rating = -1)                               AS thumbs_down,
        COUNT(*)                                                            AS total,
        GROUPING(COALESCE(m.extras -> 'meta' ->> 'persona_id', 'unknown'))  AS is_total
    FROM message_feedback f
    JOIN messages m ON m.id = f.message_id
    WHERE f.updated_at >= now() - interval '7 days'  -- window
    GROUP BY ROLLUP (COALESCE(m.extras -> 'meta' ->> 'persona_id', 'unknown'))
)
SELECT
    COALESCE(persona, 'ALL')                                      AS persona,
    thumbs_up,
    thumbs_down,
    total,
    CASE WHEN total = 0 THEN 0.0
         ELSE ROUND(100.0 * thumbs_down / total, 1)
    END                                                            AS pct_down
FROM stats
ORDER BY is_total, persona;


-- ----------------------------------------------------------------------------
-- Query 2: Which reasons show up most often on 👎 votes in the last 7 days?
-- 'unsafe' always sorts first (highest priority to review), then by count.
-- ----------------------------------------------------------------------------
SELECT
    reason,
    COUNT(*) AS reason_count
FROM message_feedback f
CROSS JOIN LATERAL unnest(f.reasons) AS reason
WHERE f.rating = -1
  AND f.updated_at >= now() - interval '7 days'  -- window
GROUP BY reason
ORDER BY (reason = 'unsafe') DESC, reason_count DESC;


-- ----------------------------------------------------------------------------
-- Query 3: Full detail of each individual 👎 vote in the last 7 days, newest
-- first — for reading actual comments and tracing a bad answer back to its
-- CloudWatch request_id. Limited to the most recent 100 votes.
-- ----------------------------------------------------------------------------
SELECT
    f.updated_at                                                AS voted_at,
    COALESCE(m.extras -> 'meta' ->> 'persona_id', 'unknown')     AS persona,
    f.reasons,
    f.comment,
    q.content                                                   AS user_question,
    m.content                                                   AS answer,
    m.extras -> 'meta' ->> 'request_id'                         AS request_id,
    m.extras -> 'meta' ->> 'grader_result'                      AS grader_result,
    (m.extras -> 'meta' ->> 'latency_ms')::int                  AS latency_ms,
    m.extras -> 'meta' ->> 'ui_locale'                          AS ui_locale,
    m.session_id,
    m.id                                                        AS message_id
FROM message_feedback f
JOIN messages m ON m.id = f.message_id
-- Why seq_id and not created_at: a turn's user message and its assistant
-- reply are written in the same INSERT batch and share an identical
-- created_at (verified in db/session_store.py's write_session_turn comments),
-- so ordering on the timestamp alone can return the reply before the
-- question. seq_id is a BIGSERIAL assigned in insert order, and the user row
-- is always inserted before the assistant row, so the largest seq_id below
-- this answer's seq_id is reliably the question that preceded it.
LEFT JOIN LATERAL (
    SELECT q.content
    FROM messages q
    WHERE q.session_id = m.session_id
      AND q.role = 'user'
      AND q.seq_id < m.seq_id
    ORDER BY q.seq_id DESC
    LIMIT 1
) q ON true
WHERE f.rating = -1
  AND f.updated_at >= now() - interval '7 days'  -- window
ORDER BY f.updated_at DESC
LIMIT 100;
