# /pr-test screenshots — #12992 + #13002 dev validation

Test artifacts for the post-merge validation of PR #12992 (copilot SDK thinking-only re-prompt + workspace storage Prisma fix) and #13002 (dynamic max_budget_usd + baseline parity), run against `dev-builder.agpt.co` after the deploy at 2026-05-05T02:17 UTC.

Login: `<dev-login>` (credentials elided per security policy).

## Scenarios

| # | Scenario | Verdict | Session | Screenshot |
|---|---|---|---|---|
| 1 | Re-prompt golden path — "best restaurants in London?" with extended thinking + web search | ✅ PASS | `1a72e9ba-583a-4b5c-9866-685ad17bc0ec` | [test1-restaurants.png](screenshots/test1-restaurants.png) |
| 2 | Prisma fix — `AIImageGeneratorBlock` rendered an image; `write_workspace_file` saved `notes.md` (11 bytes); zero `ClientNotConnectedError` in copilot-executor logs since deploy | ✅ PASS | `1380e1d6-2491-4354-88b0-f7da0ce17ff0` | [test2-image-gen.png](screenshots/test2-image-gen.png) |
| 3 | Multi-tool reasoning — top-5 starred Rust repos via web search, summarised | ✅ PASS | `1380e1d6-...` (same session, follow-up) | [test3-multi-tool.png](screenshots/test3-multi-tool.png) |
| 4 | SDK dynamic `max_budget_usd` — Langfuse trace inspection | _pending_ | — | — |
| 5 | Baseline `<budget_context>` — best-effort, gated on UI/flag access | _pending_ | — | — |
| 6 | Plain Q&A regression | _pending_ | — | — |
| 7 | Refresh / `--resume` regression | _pending_ | — | — |

## Evidence summary

- **Issue 5 (re-prompt):** Test 1 produced a 4347-character structured restaurant list with neighborhood grouping. "Thought for 2m 16s" indicates extended thinking was active — exactly the condition that produced the original `(Done — no further commentary.)` placeholder. No placeholder appeared.
- **Issue 2 (Prisma fix):** `AIImageGeneratorBlock` (the headline-failing block from the original Discord report) ran end-to-end and rendered an image inline. `write_workspace_file` succeeded with "Wrote notes.md to workspace (11 bytes)". `gcloud logging read` against `dev-agpt` namespace for `ClientNotConnectedError` since deploy: zero matches.

## Verdict so far

**SAFE IN DEV** for the two headline issues. Tests 4-7 still in progress; this README will be updated as results land.
