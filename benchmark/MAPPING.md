# FQC → Retail Benchmark Mapping

Translates the FQC Chat-Demo benchmark suite (29 tests, `alerts` collection/table)
into the equivalent for this retail text-to-SQL system (`transactions` /
`transaction_items` / `products` / `staff` / `customers` / `footfall`).

## Domain vocabulary

| FQC concept | Retail equivalent | Why |
|---|---|---|
| `alerts` row (one inspection event) | `transactions` row (one bill) | Both are the one-row-per-event log table everything else is measured against. |
| `alert_type` (HAND_TOUCH / MISSING_CLEANING / FAST_INSPECTION / NORMAL_OPERATION) | `exception_type` (HIGH_DISCOUNT / VOID_WITHOUT_SCAN / MANUAL_PRICE_OVERRIDE / non-exception) | 4 categories each; retail's 4th (`is_exception = 0`) plays the role of `NORMAL_OPERATION` — the common, non-anomalous case. `NO_SALE_OPEN` has no FQC counterpart and is left untested by this mapping. |
| — `HAND_TOUCH` | → `HIGH_DISCOUNT` | Arbitrary but fixed 1:1 pairing, used consistently everywhere below. |
| — `MISSING_CLEANING` | → `VOID_WITHOUT_SCAN` | ” |
| — `FAST_INSPECTION` | → `MANUAL_PRICE_OVERRIDE` | ” |
| — `NORMAL_OPERATION` | → non-exception bill (`is_exception = 0`) | ” |
| `zone` (physical inspection area) | `cashier` (who rang up the bill) | Both are the "which station/person" dimension a per-event log can be grouped by. |
| `FQC Station 1` (a specific zone) | a specific cashier, **Priya Nair** | Picked once, used everywhere a named station appears. |
| `inspection_time` | `bill_seconds` | Both are a per-event duration column. |
| dashboard endpoints (`/dashboard/fqc`, `/stats/summary`, `/alerts/recent`) | `/dashboard`, `/bill/{n}` | Retail only has one dashboard endpoint + a per-record lookup; both are hit. |
| BFCL function-calling (official harness, tool schemas) | the `find_product` tool call | Retail's only model-invoked tool. No official retail BFCL dataset exists — adapted to a hand-built product-lookup-vs-plain-SQL decision set (~40 Qs), explicitly **not** a BFCL replication. |
| Mongo backup/restore | SQLite `.backup` + `PRAGMA integrity_check` | This system is SQLite-only; the Mongo half of Test #27 is dropped, not adapted (no Mongo deployment exists here to test). |
| 31.5M-row / 10-year synthesized volume (Test #8/#15) | a 10×-scaled synthetic copy of `retail.db` (~350K transactions) generated in an isolated file | Full 31.5M-row parity would need a multi-GB synthetic DB and hours of generation; scaled down and reported as such rather than silently matching the original's row count. |
| RAPL host power + `nvidia-smi` GPU power | `nvidia-smi` only, sampled over SSH on the live NVIDIA host (192.168.0.21) | No Intel RAPL access from this environment; GPU power (the dominant term for an LLM workload) is still measured for real. |
| Playwright browser automation (Test #18) | plain HTTP calls to `/chat` (same requests the widget itself makes) | No Playwright/Chromium available in this environment; the network-level behavior is identical, just not driven through a rendered DOM. |

## Test #1 / #2 question translation (also reused by #7, #8, #13 subset)

| # | FQC question | Retail question |
|---|---|---|
| 1 | how many alerts today | how many bills today |
| 2 | how many hand touch alerts yesterday | how many high discount exceptions yesterday |
| 3 | how many missing cleaning alerts this week | how many void without scan exceptions this week |
| 4 | how many alerts in total | how many bills in total |
| 5 | how many fast inspection alerts at FQC Station 1 | how many manual price override exceptions handled by Priya Nair |
| 6 | how many normal operation events have been logged | how many non-exception bills have been logged |
| 7 | give me a breakdown of alert types in the last 7 days | give me a breakdown of exception types in the last 7 days |
| 8 | how many alerts of each type happened today | how many bills of each exception type happened today |
| 9 | average inspection time for hand touch alerts | average billing time for high discount exceptions |
| 10 | average inspection time for fast inspection alerts this month | average billing time for manual price override exceptions this month |
| 11 | which day had the most hand touch alerts | which day had the most high discount exceptions |
| 12 | which zone had the most missing cleaning alerts | which cashier had the most void without scan exceptions |
| 13 | how many alerts between 2pm and 4pm | how many bills between 2pm and 4pm |
| 14 | how many alerts on september 19th 2023 | how many bills on march 19th 2026 |
| 15 | how many alerts this year | how many bills this year |
| 16 | how many alerts this month | how many bills this month |
| 17 | count the hand touch alerts this year | count the high discount exceptions this year |
| 18 | how many missing cleaning alerts were there this month | how many void without scan exceptions were there this month |
| 19 | compare fast inspection and hand touch counts for this week vs last week | compare manual price override and high discount exception counts for this week vs last week |
| 20 | give me a quarterly report | give me a quarterly report |
| 21 | give me a weekly report | give me a weekly report |
| 22 | give me a monthly report for september 2026 | give me a monthly report for september 2026 |
| 23 | how many distinct alert types are there | how many distinct exception types are there |
| 24 | which days did we get 328 alerts | which days did we get 90 bills |
| 25 | how many alerts on the last 999999999 days (overflow edge) | how many bills in the last 999999999 days |
| 26 | how many alerts in the year 3000 (empty-result edge) | how many bills in the year 3000 |
| 27 | what's the total inspection time across all alerts | what's the total billing time across all bills |
| 28 | which day of the week has the most hand touch alerts | which day of the week has the most high discount exceptions |
| 29 | how many alerts happened at hour 14 | how many bills happened at hour 14 |
| 30 | average inspection time by zone | average billing time by cashier |
| 31 | total alerts vs missing cleaning alerts | total bills vs void without scan exceptions |
| 32 | how many alerts in Q1 2026 | how many bills in Q1 2026 |
| 33 | how many alerts in the last 30 days | how many bills in the last 30 days |
| 34 | how many alerts in the last 4 weeks | how many bills in the last 4 weeks |
| 35 | breakdown of alerts by zone | breakdown of bills by cashier |
| 36 | how many hand touch alerts happened at hour 14 | how many high discount exceptions happened at hour 14 |
| 37 | give me a report for last quarter | give me a report for last quarter |
| 38 | how many alerts between march 1 2026 and march 31 2026 | how many bills between march 1 2026 and march 31 2026 |
| 39 | which day had the fewest alerts | which day had the fewest bills |
| 40 | how many alerts today vs yesterday | how many bills today vs yesterday |

Tests #3 (safety), #4 (refusal), #12 (tricky), #26 (timezone), #28
(localization) keep the FQC adversarial/edge-case *shape* but retarget the
in-scope noun from "alerts" to "bills"/"revenue" — see `run_full_suite.py`
for the exact retail-worded lists (mechanical find/replace, not worth a
second full table here).

## Tests dropped or heavily adapted, and why

| # | FQC test | Disposition |
|---|---|---|
| 16 | RBAC | Skipped — not implemented in either system (same as FQC's own status). |
| 17 | RBAC bypass | Skipped — depends on #16. |
| 20 | BFCL function-calling accuracy | Adapted to a `find_product` tool-call decision set (~40 Qs), not the official BFCL v4 dataset. |
| 22 | Official MLPerf Edge Agentic | Same adaptation as #20 reused; not a real MLPerf submission-grade run. |
| 27 | Backup/restore (Mongo + SQLite) | SQLite half only; no Mongo deployment exists for this project. |
