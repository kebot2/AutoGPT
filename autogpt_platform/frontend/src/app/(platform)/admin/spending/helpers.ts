import type { CopilotWeeklyUsageRow } from "@/app/api/__generated__/models/copilotWeeklyUsageRow";
import type { UserTransaction } from "@/app/api/__generated__/models/userTransaction";

const MICRODOLLARS_PER_USD = 1_000_000;
const CREDIT_CENTS_PER_USD = 100;

function csvEscape(val: unknown): string {
  const s = val == null ? "" : String(val);
  return `"${s.replace(/"/g, '""')}"`;
}

const CREDIT_CSV_HEADERS = [
  "transaction_id",
  "user_id",
  "user_email",
  "created_at",
  "type",
  "amount_usd",
  "running_balance_usd",
  "admin_user_id",
  "admin_email",
  "reason",
];

export function buildCreditTransactionsCsv(rows: UserTransaction[]): string {
  const header = CREDIT_CSV_HEADERS.map(csvEscape).join(",");
  const body = rows.map((tx) =>
    [
      tx.transaction_key,
      tx.user_id,
      tx.user_email,
      tx.transaction_time,
      tx.transaction_type,
      ((tx.amount ?? 0) / CREDIT_CENTS_PER_USD).toFixed(2),
      ((tx.running_balance ?? 0) / CREDIT_CENTS_PER_USD).toFixed(2),
      // admin_user_id is not surfaced separately; admin_email already encodes the
      // identity, so leave the column blank rather than duplicating data.
      "",
      tx.admin_email,
      tx.reason,
    ]
      .map(csvEscape)
      .join(","),
  );
  return [header, ...body].join("\r\n");
}

const COPILOT_CSV_HEADERS = [
  "user_id",
  "user_email",
  "week_start",
  "week_end",
  "copilot_cost_usd",
  "tier",
  "weekly_limit_usd",
  "percent_used",
];

export function buildCopilotUsageCsv(rows: CopilotWeeklyUsageRow[]): string {
  const header = COPILOT_CSV_HEADERS.map(csvEscape).join(",");
  const body = rows.map((row) =>
    [
      row.user_id,
      row.user_email,
      row.week_start,
      row.week_end,
      (row.copilot_cost_microdollars / MICRODOLLARS_PER_USD).toFixed(6),
      row.tier,
      (row.weekly_limit_microdollars / MICRODOLLARS_PER_USD).toFixed(2),
      row.percent_used.toFixed(2),
    ]
      .map(csvEscape)
      .join(","),
  );
  return [header, ...body].join("\r\n");
}

export function downloadCsv(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// "YYYY-MM-DD" -> ISO timestamp at UTC midnight.
export function dateInputToUtcIso(input: string): string {
  if (!input) return "";
  return new Date(`${input}T00:00:00Z`).toISOString();
}

// Same conversion but pinned to end-of-day so the inclusive `end` filter
// covers the entire selected day.
export function dateInputToUtcIsoEnd(input: string): string {
  if (!input) return "";
  return new Date(`${input}T23:59:59.999Z`).toISOString();
}

export function defaultStartDate(): string {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return d.toISOString().slice(0, 10);
}

export function defaultEndDate(): string {
  return new Date().toISOString().slice(0, 10);
}
