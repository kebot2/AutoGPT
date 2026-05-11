-- Session-level lifecycle for the per-user soft running cap +
-- cross-session queue.  ``chatStatus`` is ``'idle'`` on every existing
-- row (the 99% case), ``'queued'`` while waiting for a running slot,
-- and ``'running'`` while a turn is being processed.  Open enum:
-- future states can be added without another migration.
ALTER TABLE "ChatSession"
    ADD COLUMN "chatStatus" TEXT NOT NULL DEFAULT 'idle';

-- Single compound index covers all three ChatSession queries:
--   * cap-count (count by userId + chatStatus)
--   * queue-list (find_many WHERE userId + chatStatus ORDER BY updatedAt)
--   * sidebar list (find_many WHERE userId ORDER BY updatedAt desc) —
--     Postgres scans the per-userId index range and sorts in memory.
-- Drop the prior (userId, updatedAt) index so the 3-col one is the
-- only path for this table's per-user queries.
DROP INDEX IF EXISTS "ChatSession_userId_updatedAt_idx";

CREATE INDEX "ChatSession_user_status_idx"
    ON "ChatSession" ("userId", "chatStatus", "updatedAt");

-- ChatMessage carries an optional per-row JSONB metadata bag for the
-- dispatcher's submit-time payload on the user row that triggered a
-- queued turn (file_ids, mode, model, permissions, context,
-- request_arrival_at).  Cleared / unused on every history row.
ALTER TABLE "ChatMessage"
    ADD COLUMN "metadata" JSONB;
