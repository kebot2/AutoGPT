-- AlterTable
ALTER TABLE "PlatformLink" ADD COLUMN "organizationId" TEXT;

-- AddForeignKey
ALTER TABLE "PlatformLink" ADD CONSTRAINT "PlatformLink_organizationId_fkey"
  FOREIGN KEY ("organizationId") REFERENCES "Organization"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- CreateIndex
CREATE INDEX "PlatformLink_organizationId_idx" ON "PlatformLink"("organizationId");
