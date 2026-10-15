"""GDPR deletion cascade — M.8 (D12, D17).

CRITICAL: a rolling summary covers messages by range, so deleting a message
it covers would leave the summary describing a message that is gone. The fix
is a reset: the summary is dropped and rebuilt from the remaining messages by
the next compaction. Deleting an uncovered message leaves the summary alone.

Key rules (M.8):
  1. Reset, not repair: a covered delete clears stm_summary/stm_up_to_seq
  2. HARD delete (not soft)
"""

from __future__ import annotations

from langgraph_agents.shared import get_pg_client
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.gdpr")


async def delete_message(message_id: str, session_id: str) -> dict:
    """Delete a single message, resetting the rolling summary when covered.

    Step 1: Get seq_id of message to delete
    Step 2: When it sits at or below stm_up_to_seq, clear the summary —
      whatever it says about that message is gone with it
    Step 3: Hard-delete the message

    Returns: {deleted: true, stm_reset: bool}
    """
    pg = get_pg_client()
    await pg.connect()

    async with pg._pool.acquire() as conn:
        async with conn.transaction():
            # Step 1: Get seq_id
            row = await conn.fetchrow(
                "SELECT seq_id FROM messages WHERE id = $1 AND session_id = $2",
                message_id, session_id,
            )
            if not row:
                return {"deleted": False, "reason": "not_found"}

            seq = row["seq_id"]

            # Step 2: Reset the rolling summary when it covers this message
            head = await conn.fetchrow(
                "SELECT stm_up_to_seq FROM conversations WHERE session_id = $1",
                session_id,
            )
            stm_reset = bool(head and seq <= (head["stm_up_to_seq"] or 0))
            if stm_reset:
                await conn.execute(
                    "UPDATE conversations "
                    "SET stm_summary = NULL, stm_up_to_seq = 0 "
                    "WHERE session_id = $1",
                    session_id,
                )

            # Step 3: Hard-delete the message (M.8 #2)
            await conn.execute(
                "DELETE FROM messages WHERE id = $1 AND session_id = $2",
                message_id, session_id,
            )

            logger.info("gdpr_delete_message", extra={
                "message_id": message_id,
                "session_id": session_id,
                "seq": seq,
                "stm_reset": stm_reset,
            })

            return {
                "deleted": True,
                "stm_reset": stm_reset,
            }


async def delete_session(session_id: str) -> dict:
    """Hard-delete an entire session and all associated data.

    FK ON DELETE CASCADE handles:
      - messages (session_id FK → CASCADE)
      - summaries (session_id FK → CASCADE)
    conversations row is deleted explicitly.
    """
    pg = get_pg_client()
    await pg.connect()

    await pg.execute(
        "DELETE FROM conversations WHERE session_id = $1",
        session_id,
    )

    logger.info("gdpr_delete_session", extra={"session_id": session_id})
    return {"deleted": True, "session_id": session_id}


async def delete_user(user_id: str) -> dict:
    """Hard-delete a user and ALL associated data (GDPR erasure).

    FK ON DELETE CASCADE handles:
      - conversations (user_id FK → CASCADE)
        - messages (session_id FK → CASCADE)
        - summaries (session_id FK → CASCADE)
      - user_memory (user_id FK → CASCADE)
    users row is deleted explicitly.

    Per D19: messages + summaries have NO user_id — cascade through conversations
    works correctly (conversations.user_id → conversations.session_id → messages/summaries).
    """
    pg = get_pg_client()
    await pg.connect()

    # Count before deletion for audit
    conv_count = await pg.fetchval(
        "SELECT COUNT(*) FROM conversations WHERE user_id = $1", user_id
    )

    await pg.execute("DELETE FROM users WHERE id = $1", user_id)

    logger.info("gdpr_delete_user", extra={
        "user_id": user_id,
        "conversations_deleted": conv_count,
    })
    return {
        "deleted": True,
        "user_id": user_id,
        "conversations_deleted": conv_count,
    }
