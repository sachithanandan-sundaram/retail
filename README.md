# Retail LLM — Natural Language Query Demo

Ask free-form business questions about a retail store's data (stock, billing,
sales trends, customer behaviour, footfall, staff performance) and get a
plain-English answer backed by a **real** SQL query — plus a live KPI dashboard.

Follow-on to the FQC visual-defect demo — and built to the **same shape as that
project's `chat/`** (see `LLM_QUERY_ARCHITECTURE_TEMPLATE.md`): a React/Vite
dashboard + streaming chat widget on the front, a FastAPI query pipeline with
per-stage lifecycle timings on the back. Retargeted from MongoDB/alerts to
SQLite/retail.

## Layout

```
Retail/
├── backend/                 Python — FastAPI API + query pipeline
│   ├── retail_llm/
│   │   ├── db.py             schema + connection
│   │   ├── generate_data.py  fabricate retail.db (1 year, hypermarket catalogue)
│   │   ├── export_mongo.py   mirror retail.db into MongoDB (for Compass)
│   │   ├── dates.py          deterministic relative-date parsing
│   │   ├── fast_paths.py     ~30 rule-based SQL templates (no LLM)
│   │   ├── schema_prompt.py  the two system prompts
│   │   ├── repair.py         SELECT-only validation + self-correction checks
│   │   ├── llm.py            shared llama.cpp instance + lock (+ streaming)
│   │   ├── pipeline.py       orchestrator: intents, stages, self-correction, streaming
│   │   ├── phrase.py         deterministic sentence + answer safety net
│   │   ├── dashboard.py      deterministic KPI aggregates
│   │   ├── server.py         FastAPI app (/chat, /chat/stream, /dashboard, /bill)
│   │   └── cli.py            terminal REPL
│   ├── tests/
│   └── requirements.txt
├── frontend/                Vite + React (mirrors the FQC chat/frontend)
│   ├── src/
│   │   ├── App.jsx  main.jsx  api.js  Logo.jsx
│   │   ├── Dashboard.jsx / .css   KPI cards, charts, tables, time window
│   │   └── ChatWidget.jsx / .css  streaming chat, result tables, SQL + stage panel
│   └── package.json
├── models/                  Qwen2.5-3B-Instruct GGUF lives here
└── retail.db                generated SQLite database (repo root)
```

## How the query path works

```
question (+ last 4 conversation turns)
  -> spelling correction        retail-domain vocabulary (categories, terms), typo-tolerant
  -> classify intent            greeting / unsupported / data_query
  -> LLM #1 writes a SQL SELECT  (schema-aware prompt, interpreted date range injected)
  -> validate + repair          (SELECT-only, single statement, row cap)
  -> run against SQLite
  -> self-correction check       DB error? needs aggregation? category filter? time grouping?
       └─ fails once ─► regenerate with the problem explained back to the model, re-run
  -> deterministic maths layer   percentage / share / margin / growth / average — Python, not the LLM
  -> LLM #2 phrases the rows (streamed token-by-token), or the maths layer's ready-made sentence
  -> deterministic safety net    no invented values, no dropped values, no false "no data"
  -> answer + per-stage lifecycle timings
```

The LLM writes every single-question query — there are no deterministic
fast-path SQL shortcuts — but it never computes an answer itself: any
percentage, share, margin, growth rate or average is worked out by
`retail_llm/maths.py` in exact Python arithmetic from the raw component
numbers the SQL returns, and that layer's sentence is used verbatim (the model
doesn't get a chance to recompute it).

A **"report"** request ("monthly sales report", "quarterly report", "weekly
report for last week", "yearly report", "daily report") is handled entirely
differently, by `retail_llm/report.py`: several targeted SQL queries (revenue
vs the previous period, top categories/products/cashiers, busiest day/hour,
exceptions, footfall) are run and composed into a multi-section summary — no
model call at all, so a report works even without the LLM loaded. The
breakdown tables ride along as `result` facets that the chat widget renders
as titled tables under the summary.

The **dashboard** (`/dashboard`) and **bill lookup** (`/bill/{n}`) paths are
100% deterministic SQL, no LLM.

The chat widget streams over Server-Sent Events (`/chat/stream`): a `meta` event
with the SQL + result the moment the query runs, then `token` events as the
answer is written, then a `done` event with the final safety-checked text. Every
answer carries a "show how this was answered" panel — the SQL plus each
pipeline stage and how long it took.

## Setup

```bash
cd backend
pip install -r requirements.txt
export RETAIL_NOW=2026-08-27T20:00:00      # pin "now" for a reproducible demo
python -m retail_llm.generate_data         # builds ../retail.db
```

### LLM backend (optional — the general query path)

Local **Qwen2.5-3B-Instruct Q4_K_M** GGUF via `llama-cpp-python`, expected at
`models/Qwen2.5-3B-Instruct-GGUF/qwen2.5-3b-instruct-q4_k_m.gguf` (auto-detected;
override with `RETAIL_GGUF_MODEL_PATH`).

The sibling `slm-main/.venv` already has a working CUDA build; run the backend
with that interpreter to enable the LLM path:

```bash
d:/WG/slm-main/.venv/Scripts/python.exe -m uvicorn retail_llm.server:app --port 8000
```

There are no fast paths — the LLM must be loaded for any data question to be
answered (the dashboard and bill lookup still work without it, since those are
separate deterministic endpoints).

## Run

```bash
# 1. build the frontend once (outputs frontend/dist/, which the backend serves)
cd frontend && npm install && npm run build

# 2. start the backend
cd ../backend
export RETAIL_NOW=2026-08-27T20:00:00
uvicorn retail_llm.server:app --port 8000        # -> http://localhost:8000

# with the local LLM query path enabled:
d:/WG/slm-main/.venv/Scripts/python.exe -m uvicorn retail_llm.server:app --port 8000
```

Terminal instead of the browser:

```bash
cd backend
python -m retail_llm.cli "which counter person has billed the most this week?"
python -m retail_llm.cli                          # interactive REPL
```

### Frontend dev server (hot reload)

```bash
cd frontend && npm run dev        # -> http://localhost:5173, talks to the API on :8000
```

`src/api.js` points at `http://127.0.0.1:8000` in dev and same-origin in the
built bundle; override with `VITE_API_BASE`. CORS is open.

## Every bill is fully itemised

`transactions` = one row per bill — **bill no** (`transaction_id`), **date & time**
(`ts`, plus `start_time` / `end_time` / `bill_seconds` for how long billing took),
**cashier** (`cashier_id → staff.name`), **customer** (`customer_id → customers`,
nullable for walk-ins), `subtotal`, `discount_pct`, `total_amount`,
`is_exception` / `exception_type`.

`transaction_items` = the line items for that bill — product, `quantity`,
`unit_price`, `line_total`.

- API: `GET /bill/{bill_no}` → header + all line items as JSON
- Chat: *"show me what was in bill #15000"*, *"who billed transaction 15000 and when"*

## Browse the data

- **SQLite:** `retail.db` — open with DB Browser for SQLite / a VS Code SQLite
  extension / DBeaver.
- **MongoDB Compass:** `python -m retail_llm.export_mongo` mirrors every table
  into the local `retail_demo` database → connect Compass to
  `mongodb://localhost:27017`. (Read-only snapshot; the pipeline still runs on
  SQLite.)

## Tests

```bash
cd backend
RETAIL_NOW=2026-08-27T20:00:00 python -m pytest -q
```

34 tests: structural checks (does the SQL aggregate / filter correctly) + value
checks (does the number match an independent direct query) against the live DB.
Pass with or without `llama-cpp-python` installed.

## Schema

| table | purpose |
|---|---|
| `products` | full-line hypermarket catalogue (~280 SKUs, 31 categories: groceries & produce → apparel, footwear, kitchen, furniture → mobiles, laptops, TVs, appliances), price/cost, `reorder_threshold`, `current_stock`, `last_sold_date` |
| `staff` | cashiers / floor / manager |
| `customers` | first/last visit, visit count, total spend |
| `transactions` | one row per bill: totals, discount, `is_exception`/`exception_type`, `bill_seconds` |
| `transaction_items` | line items |
| `footfall` | hourly store entry counts (synthetic, realistic peak-hour shape) |

### Synthetic-data notes
- Modelled on a DMart / Reliance-style hypermarket: daily-needs categories
  drive **unit volume** (groceries, produce, beverages, snacks each 13k–20k
  units/yr), while electronics and furniture are rare but drive **revenue** and
  produce the big bills (₹1–3 lakh). 1 year of history, ~35k bills,
  avg bill ≈ ₹4,000, median ≈ ₹1,600.
- Big-ticket lines (mobiles, laptops, TVs, large appliances, furniture) sell
  quantity 1, carry small stock, and low reorder thresholds.
- Footfall and `bill_seconds` have no real sensor source — generated with
  plausible shapes (mid-morning & weekend-evening peaks; 35–70 s billing with
  ~3 % long outliers).
- Billing exceptions (~2 % of bills) are flagged transaction rows
  (`VOID_WITHOUT_SCAN`, `HIGH_DISCOUNT`, `MANUAL_PRICE_OVERRIDE`, `NO_SALE_OPEN`).
- 15 products are seeded as guaranteed dead stock.

## Out of scope (future phase)
"Suspected shoplifting incidents" — needs a CCTV vision pipeline, not
structured-DB data.

## Known limitations
- Multi-window comparisons beyond the built-in "X vs previous X" revenue case
  rely on the LLM path.
- Product / staff name matching is `LIKE '%term%'` — ambiguous names return
  multiple rows.
