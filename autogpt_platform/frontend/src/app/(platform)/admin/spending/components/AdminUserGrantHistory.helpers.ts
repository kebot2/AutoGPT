import { CreditTransactionType } from "@/lib/autogpt-server-api";

export type RowColor = "text-green-600" | "text-blue-600" | "text-red-600";

/**
 * Pick the row text color for a transaction amount.
 *
 * REFUND is overloaded: block-execute refunds write a positive amount
 * (user-facing credit return → green), Stripe clawbacks write a negative
 * amount (deduction → red). Branch on sign so admins can tell them
 * apart at a glance.
 */
export function pickAmountColor(
  amount: number,
  type: CreditTransactionType,
): RowColor {
  const isPositive =
    type === CreditTransactionType.GRANT ||
    (type === CreditTransactionType.REFUND && amount > 0);
  const isNeutral = type === CreditTransactionType.TOP_UP;
  if (isPositive) return "text-green-600";
  if (isNeutral) return "text-blue-600";
  return "text-red-600";
}

export type TypePillColor =
  | "bg-green-100 text-green-800"
  | "bg-blue-100 text-blue-800"
  | "bg-red-100 text-red-800"
  | "";

/**
 * Pick the background color for the transaction-type pill. Mirrors
 * ``pickAmountColor`` REFUND-sign branching so the pill and the amount
 * stay in agreement (green refund credit / red refund clawback).
 */
export function pickTypePillColor(
  type: CreditTransactionType,
  amount: number,
): TypePillColor {
  const isGrant = type === CreditTransactionType.GRANT;
  const isPurchased = type === CreditTransactionType.TOP_UP;
  const isSpent = type === CreditTransactionType.USAGE;
  const isRefundCredit = type === CreditTransactionType.REFUND && amount > 0;
  const isRefundClawback = type === CreditTransactionType.REFUND && amount < 0;

  if (isGrant || isRefundCredit) return "bg-green-100 text-green-800";
  if (isPurchased) return "bg-blue-100 text-blue-800";
  if (isSpent || isRefundClawback) return "bg-red-100 text-red-800";
  return "";
}
