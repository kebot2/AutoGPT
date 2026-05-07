import { describe, expect, test } from "vitest";

import { CreditTransactionType } from "@/lib/autogpt-server-api";

import {
  pickAmountColor,
  pickTypePillColor,
} from "../AdminUserGrantHistory.helpers";

describe("pickAmountColor", () => {
  test("GRANT renders green regardless of sign", () => {
    expect(pickAmountColor(500, CreditTransactionType.GRANT)).toBe(
      "text-green-600",
    );
  });

  test("TOP_UP renders blue (neutral)", () => {
    expect(pickAmountColor(1000, CreditTransactionType.TOP_UP)).toBe(
      "text-blue-600",
    );
  });

  test("USAGE renders red (spent)", () => {
    expect(pickAmountColor(-42, CreditTransactionType.USAGE)).toBe(
      "text-red-600",
    );
  });

  test("REFUND with positive amount renders green (block-execute refund)", () => {
    expect(pickAmountColor(42, CreditTransactionType.REFUND)).toBe(
      "text-green-600",
    );
  });

  test("REFUND with negative amount renders red (Stripe clawback)", () => {
    expect(pickAmountColor(-1000, CreditTransactionType.REFUND)).toBe(
      "text-red-600",
    );
  });
});

describe("pickTypePillColor", () => {
  test("GRANT pill is green", () => {
    expect(pickTypePillColor(CreditTransactionType.GRANT, 500)).toBe(
      "bg-green-100 text-green-800",
    );
  });

  test("TOP_UP pill is blue", () => {
    expect(pickTypePillColor(CreditTransactionType.TOP_UP, 1000)).toBe(
      "bg-blue-100 text-blue-800",
    );
  });

  test("USAGE pill is red", () => {
    expect(pickTypePillColor(CreditTransactionType.USAGE, -42)).toBe(
      "bg-red-100 text-red-800",
    );
  });

  test("REFUND with positive amount pills green (block-execute refund)", () => {
    expect(pickTypePillColor(CreditTransactionType.REFUND, 42)).toBe(
      "bg-green-100 text-green-800",
    );
  });

  test("REFUND with negative amount pills red (Stripe clawback)", () => {
    expect(pickTypePillColor(CreditTransactionType.REFUND, -1000)).toBe(
      "bg-red-100 text-red-800",
    );
  });

  test("REFUND with zero amount falls back to empty pill", () => {
    expect(pickTypePillColor(CreditTransactionType.REFUND, 0)).toBe("");
  });
});
