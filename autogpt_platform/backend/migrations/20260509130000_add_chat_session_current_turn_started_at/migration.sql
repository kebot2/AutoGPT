-- AlterTable
ALTER TABLE "ChatSession"
    ADD COLUMN "currentTurnStartedAt" TIMESTAMP(3);

-- Partial index for the per-user running-turn count on the cap path.
-- WHERE currentTurnStartedAt IS NOT NULL keeps it tiny since the column
-- is NULL on every idle session (the 99% case).
CREATE INDEX "ChatSession_running_turns_idx"
    ON "ChatSession" ("userId", "currentTurnStartedAt")
    WHERE "currentTurnStartedAt" IS NOT NULL;
