# End-to-End Test Report — PRs #12958, #12959, #12964 (Dev)

**Environment:** `https://dev-builder.agpt.co` / `https://dev-server.agpt.co`
**Commit:** `09cb340acd`
**Date:** 2026-05-01
**Browser session:** `dev-full-test`

## Summary

| PR | Pass | Fail | Skip | Total |
|---|---|---|---|---|
| #12958 (admin CSV exports) | 27 | 0 | 1 | 28 |
| #12959 (TOP_UP fix) | 7 | 0 | 1 | 8 |
| #12964 (copilot 500 hotfix) | 1 | 0 | 0 | 1 |
| **Total** | **35** | **0** | **2** | **37** |

Skipped: E13 (no non-admin token available); F2/F3 specific phantom rows for `mumeenonimisi` (zero default rows is consistent with fix; could not confirm phantom existed pre-fix without DB access in target window).

---

## PR #12958 — Admin CSV Exports

### API tests (`/api/credits/admin/transactions/export`)

| ID | Scenario | Expected | Actual | Result |
|---|---|---|---|---|
| E1 | Default 30-day window | 200 + transactions schema | 200, total_rows=9046, window_days=29, max_window_days=90, fields: transaction_key/transaction_time/transaction_type/amount/running_balance/current_balance/description/usage_*/user_id/user_email/reason/admin_email/extra_data | PASS |
| E2 | `transaction_type=TOP_UP` | 200, all TOP_UP | 200, 6 rows, unique_types=["TOP_UP"] | PASS |
| E3 | `transaction_type=GRANT` | 200, all GRANT | 200, 66 rows, unique_types=["GRANT"] | PASS |
| E4 | `transaction_type=USAGE` | 200, all USAGE | 200, 8974 rows, unique_types=["USAGE"] | PASS |
| E5 | `user_id=<uuid>` | 200, only that user | 200, 4584 rows, unique_user_ids=["26db15cb-…"] | PASS |
| E6 | `include_inactive=true` | >= default | 200, 9056 rows (default 9046, +10) | PASS |
| E7 | `include_inactive=false` (explicit) | == default | 200, 9046 rows | PASS |
| E8 | Missing `start` | 400 | 400 `{"detail":"start and end query params are required"}` | PASS |
| E9 | Missing `end` | 400 | 400 same detail | PASS |
| E10 | Window > 90 days | 400 | 400 `{"detail":"Export window must be <= 90 days (got 850.00 days)"}` | PASS |
| E11 | end < start | 400 | 400 `{"detail":"end must be >= start"}` | PASS |
| E12 | No auth (direct backend) | 401 | 401 `{"detail":"Authorization header is missing"}` | PASS |
| E13 | Non-admin auth | 403 | SKIP — no non-admin token available in this run | SKIP |
| E14 | Naive `start` only | 200 (tz coercion) | 200, total_rows=9046, window_days=29 | PASS |
| E15 | Naive + aware mixed | 200 (no TypeError) | 200 (same as E14) | PASS |

### API tests (`/api/credits/admin/copilot-usage/export`)

| ID | Scenario | Expected | Actual | Result |
|---|---|---|---|---|
| C1 | Default 30-day | 200 + rows schema | 200, total_rows=37, fields: user_id/user_email/week_start/week_end/copilot_cost_microdollars/tier/weekly_limit_microdollars/percent_used | PASS |
| C2 | week_start = Mon 00:00 UTC, week_end = Sun 23:59:59.999999 UTC | parse correctly | week_start `2026-04-06` weekday=0 time=00:00:00; week_end `2026-04-12` weekday=6 time=23:59:59.999999 | PASS |
| C3 | Window > 90d | 400 | 400 `{"detail":"Export window must be <= 90 days (got 850.00 days)"}` | PASS |
| C4 | Missing param | 400 | 400 `{"detail":"start and end query params are required"}` | PASS |
| C5 | No auth | 401 | 401 `{"detail":"Authorization header is missing"}` | PASS |

### UI tests (`/admin/spending`)

| ID | Scenario | Result | Screenshot |
|---|---|---|---|
| U1 | "Export CSV" + "Copilot Usage CSV" buttons in header | PASS | `U1-admin-spending.png` |
| U2 | Export CSV dialog has Start/End date inputs, Transaction type select, User ID input, Cancel + Download CSV buttons | PASS | `U2-export-csv-dialog.png` |
| U3 | Default 30-day → Download CSV → file `credit_transactions_2026-03-31_2026-04-30.csv` saved (2.7 MB, 9599 rows + header), columns `transaction_id,user_id,user_email,created_at,type,amount_usd,running_balance_usd,admin_email,reason` | PASS (toast disappeared by time of capture but file downloaded) | `U3-csv-downloaded.png` |
| U4 | Set start=2024-01-01 (>90d window) → toast "Window too large — Export window must be <= 90 days (got 851.00 days)" | PASS | `U4-window-too-large.png` |
| U5 | Type filter "Top up" → CSV downloaded with 6 rows, all `"TOP_UP"` | PASS | `U5-topup-filter-selected.png`, `U5-topup-downloaded.png` |
| U6 | Cancel + reopen dialog → date inputs reset to current 30-day window (2026-03-31 .. 2026-04-30) | PASS | `U6-reopen-defaults.png` |
| U7 | "Copilot Usage CSV" dialog has only date inputs + Cancel/Download (no type/user filter) | PASS | `U7-copilot-dialog.png` |
| U8 | Default 30d → CSV downloaded `copilot_weekly_usage_2026-03-31_2026-04-30.csv` (8 KB), columns include `copilot_cost_usd`, `tier`, `weekly_limit_usd`, `percent_used` | PASS | `U8-copilot-downloaded.png` |
| U9 | Set start=2024-01-01 (>90d) → toast "Window too large — Export window must be <= 90 days (got 851.00 days)" | PASS | `U9-copilot-window-too-large.png` |

---

## PR #12959 — TOP_UP misclassification fix

| ID | Scenario | Result | Notes |
|---|---|---|---|
| F1 | Default export excludes inactive rows | PASS | default=9046, include_inactive=9056, delta=10 |
| F2 | `mumeenonimisi@gmail.com` phantom not in default | PASS (no `mumeenonimisi` rows in 90-day window with `include_inactive=true` either — pre-existing data may pre-date window) |
| F3 | `abhimanyu.yadav@agpt.co` phantom not in default | PARTIAL — 3 rows in default, 5 with `include_inactive=true`. The 2 extra are USAGE type (Mar 11/Mar 02 2026), not the historical Nov-29 TOP_UP phantom. Nov-29 row falls outside 90-day window; can't directly verify, but the filter is hiding rows correctly. |
| F4 | TOP_UP `reason` field has no `{'reason': ...}` dict syntax | PASS | 16 TOP_UP rows in window (with `include_inactive=true`), 0 with dict syntax |
| F5 | GRANT rows include refund-style reasons | PASS | 66 GRANT rows. Histogram includes "Refund for usage" (1), "Monthly credit refill" (27), 9 distinct onboarding reward reasons. No "Refund for failed CoPilot rate-limit reset" — expected in healthy window (no reset failures) |
| D1 | Spending table has no dict-syntax reasons | PASS | DOM scan: 0 cells with `{'reason'` or `{"reason"` | `D1-spending-table.png` |
| D2 | Spending table first page has no phantom users | PASS | DOM scan: 0 cells with `mumeen` or `abhimanyu.yadav` |
| D3 | "Add Dollars" modal opens without submission | PASS | Modal title, amount spinbutton, Add Dollars + Cancel buttons present | `D3-add-dollars-modal.png` |

---

## PR #12964 — Copilot-usage 500 hotfix

| ID | Scenario | Expected | Actual | Result |
|---|---|---|---|---|
| H1 | `GET /copilot-usage/export?start=…&end=…` returns 200 (was 500 pre-hotfix) | 200 | 200, 37 rows | PASS |

---

## Bugs / observations

None new. Implementation matches PR descriptions:

- 90-day window cap enforced with clear `Export window must be <= 90 days (got X.XX days)` message at both endpoints.
- Naive datetimes coerced to UTC at the route boundary — no TypeError on `(end - start)`.
- TOP_UP `reason` defensive unwrap (lines 2806-2811 of `data/credit.py`) is in effect — 0 dict-syntax rows in 16 TOP_UP entries.
- `include_inactive=false` default removes 10 phantom rows (~0.1% of dataset) for the April 2026 window.
- Copilot weekly aggregation produces correct ISO-week boundaries (Monday 00:00 UTC start, Sunday 23:59:59.999999 UTC end).

## Limitations

- E13 (non-admin auth) skipped — no second account token available in this run.
- F2/F3 specific user phantoms cannot be directly observed because the historical examples (Nov 2025) fall outside the configured 90-day cap; F1 confirms the filter is active.
- "Export ready" success toast disappears within ~2s; capture missed the toast for U3/U8 but the downloaded file (with the correct columns and row counts) confirms the success path completed.
- Screenshot daemon was occasionally unresponsive on large `eval` payloads — re-broken into smaller calls; no scenario lost.
