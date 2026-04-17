import type { CoPilotUsageStatus } from "@/app/api/__generated__/models/coPilotUsageStatus";
import { Button } from "@/components/atoms/Button/Button";
import Link from "next/link";
import { formatCents, formatResetTime } from "../usageHelpers";
import { useResetRateLimit } from "../../hooks/useResetRateLimit";
import { useWorkspaceStorage } from "./useWorkspaceStorage";

export { formatResetTime };

function UsageBar({
  label,
  used,
  limit,
  resetsAt,
}: {
  label: string;
  used: number;
  limit: number;
  resetsAt: Date | string;
}) {
  if (limit <= 0) return null;

  const rawPercent = (used / limit) * 100;
  const percent = Math.min(100, Math.round(rawPercent));
  const isHigh = percent >= 80;
  const percentLabel =
    used > 0 && percent === 0 ? "<1% used" : `${percent}% used`;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-medium text-neutral-700">{label}</span>
        <span className="text-[11px] tabular-nums text-neutral-500">
          {percentLabel}
        </span>
      </div>
      <div className="text-[10px] text-neutral-400">
        Resets {formatResetTime(resetsAt)}
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-200">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ease-out ${
            isHigh ? "bg-orange-500" : "bg-blue-500"
          }`}
          style={{ width: `${Math.max(used > 0 ? 1 : 0, percent)}%` }}
        />
      </div>
    </div>
  );
}

export function formatBytes(bytes: number): string {
  const KB = 1024;
  const MB = KB * 1024;
  const GB = MB * 1024;
  if (bytes < KB) return `${bytes} B`;
  if (bytes < MB) {
    const kb = Math.round(bytes / KB);
    return kb >= 1024 ? `${(bytes / MB).toFixed(1)} MB` : `${kb} KB`;
  }
  if (bytes < GB) {
    const mb = Math.round(bytes / MB);
    return mb >= 1024 ? `${(bytes / GB).toFixed(1)} GB` : `${mb} MB`;
  }
  return `${(bytes / GB).toFixed(1)} GB`;
}

function StorageBar({
  usedBytes,
  limitBytes,
  fileCount,
}: {
  usedBytes: number;
  limitBytes: number;
  fileCount: number;
}) {
  if (limitBytes <= 0) return null;

  const rawPercent = (usedBytes / limitBytes) * 100;
  const percent = Math.min(100, Math.round(rawPercent));
  const isHigh = percent >= 80;
  const percentLabel =
    usedBytes > 0 && percent === 0 ? "<1% used" : `${percent}% used`;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-medium text-neutral-700">
          File storage
        </span>
        <span className="text-[11px] tabular-nums text-neutral-500">
          {percentLabel}
        </span>
      </div>
      <div className="text-[10px] text-neutral-400">
        {formatBytes(usedBytes)} of {formatBytes(limitBytes)} &middot;{" "}
        {fileCount} {fileCount === 1 ? "file" : "files"}
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-200">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ease-out ${
            isHigh ? "bg-orange-500" : "bg-blue-500"
          }`}
          style={{ width: `${Math.max(usedBytes > 0 ? 1 : 0, percent)}%` }}
        />
      </div>
    </div>
  );
}

function ResetButton({
  cost,
  onCreditChange,
}: {
  cost: number;
  onCreditChange?: () => void;
}) {
  const { resetUsage, isPending } = useResetRateLimit({ onCreditChange });

  return (
    <Button
      variant="primary"
      size="small"
      onClick={() => resetUsage()}
      loading={isPending}
      className="mt-1 w-full text-[11px]"
    >
      {isPending
        ? "Resetting..."
        : `Reset daily limit for ${formatCents(cost)}`}
    </Button>
  );
}

function WorkspaceStorageSection() {
  const { data: storage } = useWorkspaceStorage();
  if (!storage || storage.limit_bytes <= 0) return null;

  return (
    <StorageBar
      usedBytes={storage.used_bytes}
      limitBytes={storage.limit_bytes}
      fileCount={storage.file_count}
    />
  );
}

export function UsagePanelContent({
  usage,
  showBillingLink = true,
  hasInsufficientCredits = false,
  isBillingEnabled = false,
  onCreditChange,
}: {
  usage: CoPilotUsageStatus;
  showBillingLink?: boolean;
  hasInsufficientCredits?: boolean;
  isBillingEnabled?: boolean;
  onCreditChange?: () => void;
}) {
  const hasDailyLimit = usage.daily.limit > 0;
  const hasWeeklyLimit = usage.weekly.limit > 0;
  const isDailyExhausted =
    hasDailyLimit && usage.daily.used >= usage.daily.limit;
  const isWeeklyExhausted =
    hasWeeklyLimit && usage.weekly.used >= usage.weekly.limit;
  const resetCost = usage.reset_cost ?? 0;

  if (!hasDailyLimit && !hasWeeklyLimit) {
    return (
      <div className="text-xs text-neutral-500">No usage limits configured</div>
    );
  }

  const tierLabel = usage.tier
    ? usage.tier.charAt(0) + usage.tier.slice(1).toLowerCase()
    : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-semibold text-neutral-800">
          Usage limits
        </span>
        {tierLabel && (
          <span className="text-[11px] text-neutral-500">{tierLabel} plan</span>
        )}
      </div>
      {hasDailyLimit && (
        <UsageBar
          label="Today"
          used={usage.daily.used}
          limit={usage.daily.limit}
          resetsAt={usage.daily.resets_at}
        />
      )}
      {hasWeeklyLimit && (
        <UsageBar
          label="This week"
          used={usage.weekly.used}
          limit={usage.weekly.limit}
          resetsAt={usage.weekly.resets_at}
        />
      )}
      <WorkspaceStorageSection />
      {isDailyExhausted &&
        !isWeeklyExhausted &&
        resetCost > 0 &&
        !hasInsufficientCredits && (
          <ResetButton cost={resetCost} onCreditChange={onCreditChange} />
        )}
      {isDailyExhausted &&
        !isWeeklyExhausted &&
        hasInsufficientCredits &&
        isBillingEnabled && (
          <Link
            href="/profile/credits"
            className="mt-1 inline-flex w-full items-center justify-center rounded-md bg-primary px-3 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90"
          >
            Add credits to reset
          </Link>
        )}
      {showBillingLink && (
        <Link
          href="/profile/credits"
          className="text-[11px] text-blue-600 hover:underline"
        >
          Learn more about usage limits
        </Link>
      )}
    </div>
  );
}
