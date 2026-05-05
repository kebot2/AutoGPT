-- Sortable UUIDv7 generator (RFC 9562). Built on gen_random_uuid()
-- (pgcrypto, already in use). The first 48 bits encode the unix
-- timestamp in milliseconds, so values are k-sortable on insert order.
--
-- NOTE: drop this function and switch to the built-in once Supabase ships a
-- PG18 image (PG18 added native `uuidv7()`). PG15 — our current pin — has
-- no native generator and no pre-bundled extension that provides one
-- (uuid-ossp tops out at v5; pg_uuidv7 isn't in Supabase's image).
CREATE OR REPLACE FUNCTION uuid_generate_v7()
RETURNS uuid
AS $$
BEGIN
  RETURN encode(
    set_bit(
      set_bit(
        overlay(
          uuid_send(gen_random_uuid())
          PLACING substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
          FROM 1 FOR 6
        ),
        52, 1
      ),
      53, 1
    ),
    'hex'
  )::uuid;
END
$$
LANGUAGE plpgsql
VOLATILE
PARALLEL SAFE;

-- Repoint existing id defaults from Prisma-client uuid()/gen_random_uuid() to uuid_generate_v7().
ALTER TABLE "UserOnboarding" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "CoPilotUnderstanding" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "UserWorkspace" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "UserWorkspaceFile" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "SharedExecutionFile" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "BuilderSearchHistory" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "ChatSession" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "ChatMessage" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentGraph" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentPreset" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "NotificationEvent" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "UserNotificationBatch" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "PushSubscription" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "LibraryAgent" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "LibraryFolder" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentNode" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentNodeLink" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentBlock" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentGraphExecution" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentNodeExecution" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AgentNodeExecutionInputOutput" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "IntegrationWebhook" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AnalyticsDetails" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "AnalyticsMetrics" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "CreditTransaction" ALTER COLUMN "transactionKey" SET DEFAULT uuid_generate_v7();
ALTER TABLE "CreditRefundRequest" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "PlatformCostLog" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "Profile" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "StoreListing" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "StoreListingVersion" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "UnifiedContentEmbedding" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "StoreListingReview" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "APIKey" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "OAuthApplication" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "OAuthAuthorizationCode" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "OAuthAccessToken" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "OAuthRefreshToken" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "PlatformLink" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "PlatformUserLink" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
ALTER TABLE "PlatformLinkToken" ALTER COLUMN "id" SET DEFAULT uuid_generate_v7();
