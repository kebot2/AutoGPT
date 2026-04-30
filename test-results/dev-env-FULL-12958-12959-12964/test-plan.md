# End-to-End Test Plan: PRs #12958, #12959, #12964 (Dev Environment)

Target: `https://dev-builder.agpt.co` frontend / `https://dev-server.agpt.co` backend
Commit: `09cb340acd` (head of `dev` after all three PRs merged)
Browser session: `dev-full-test` (`agent-browser` CLI)
Account: `zamil.majdy@gmail.com` (admin)

## Coverage

### PR #12958 — Admin CSV exports
- E1–E15: `GET /api/credits/admin/transactions/export` happy path, filters (`transaction_type`, `user_id`, `include_inactive`), and validation (missing params, oversized window, end<start, no auth, naive datetime)
- C1–C5: `GET /api/credits/admin/copilot-usage/export` schema, week-boundary correctness, validation
- U1–U9: Admin spending UI — buttons present, dialog fields, default 30-day window, type filter, date reset on reopen, oversized-window toast, copilot dialog has only date inputs

### PR #12959 — TOP_UP misclassification fix
- F1: Default export hides inactive rows; `include_inactive=true` returns more
- F2/F3: Phantom rows (`mumeenonimisi`, `abhimanyu.yadav`) absent from default view
- F4: TOP_UP rows in window have no `{'reason': ...}` dict syntax in `reason` column
- F5: GRANT rows include refund-style entries (e.g. "Refund for usage", "Monthly credit refill")
- D1–D3: UI table has no dict-syntax reasons, no phantom users on first page, "Add Dollars" modal still opens

### PR #12964 — Copilot-usage 500 hotfix
- H1: `/copilot-usage/export` returns 200 instead of 500 (verified via C1)
