# Reusable LLM-Query-Writing Architecture — Template

This is a generalized version of the architecture built for the factory safety alert chat system, written so it can be reused for a **new domain** (e.g. retail) by swapping the domain-specific pieces and keeping the structural pieces as-is. Sections are marked **[REUSE AS-IS]**, **[REUSE THE PATTERN, REWRITE THE CONTENT]**, or **[DOMAIN-SPECIFIC — WRITE FROM SCRATCH]** so it's clear what work is actually needed for a new use case.

---

## 1. The core idea (why this architecture exists)

**Problem**: users ask free-form natural-language questions about data in a database. There's no way to pre-write every possible query in advance — the space of things someone might ask is unlimited.

**Approach**: don't ask the LLM to *answer* the question directly (it will hallucinate numbers). Instead:
1. Ask the LLM to write a **real, structured database query** for the question.
2. **Run that query for real** against the actual database.
3. Ask the LLM a *second* time to turn the real result rows into a plain-English sentence.

This works for **any domain with a queryable database** — factory alerts, retail orders, support tickets, anything. Nothing about this core idea is alert-system-specific.

```
User question
      ↓
[FAST PATH CHECK] — does this match a known, unambiguous shape? → yes → build query in code, skip LLM entirely
      ↓ no
LLM call #1: "write me a query for this question"      ← QUERY_SYSTEM_PROMPT (domain-specific)
      ↓
[REPAIR/VALIDATE] — fix known model mistakes deterministically
      ↓
Run the query for real against the database
      ↓
[SELF-CORRECTION CHECK] — does the pipeline actually match what was asked? → no → regenerate once, with the specific problem explained back to the model
      ↓ yes
LLM call #2: "here are the real result rows, phrase this as a sentence"   ← ANSWER_SYSTEM_PROMPT (domain-specific)
      ↓
Answer shown to user
```

---

## 2. Model & infra stack **[REUSE AS-IS]**

This part is fully domain-independent — the same setup works for retail without any changes:

| Component | Choice | Why |
|---|---|---|
| Base model | Qwen2.5-3B-Instruct | Small enough to run on a modest GPU (as little as 4GB VRAM), capable enough to write structured JSON reliably *with* the scaffolding below |
| Format | GGUF | A file format built for efficient inference (not the format models train in) |
| Quantization | Q4_K_M (4-bit) | Shrinks the model ~4x with a small accuracy cost — makes it fit on small hardware |
| Inference engine | `llama.cpp` (via `llama-cpp-python`) | Feeds quantized weights directly into the GPU's fast math path — measured ~5-6x faster than the alternative (transformers + bitsandbytes) on the same hardware |
| Context window | `n_ctx=4096` (model supports up to 32,768 natively) | Sized to comfortably fit the system prompt + conversation history + question + output, with headroom |

```python
from llama_cpp import Llama

_llm = Llama(
    model_path=GGUF_MODEL_PATH,   # local .gguf file
    n_gpu_layers=-1,               # offload everything to GPU
    n_ctx=4096,
    verbose=False,
)
```

**One real gotcha to carry over**: a single shared model instance is NOT safe for concurrent calls — two overlapping requests on the same `Llama` instance can crash the whole process with a hard C-level assertion. Wrap every call (including model *loading*) in one shared lock:

```python
_llm_lock = threading.Lock()   # guards both model loading AND every inference call
```

**Generation settings**:
```python
# Query generation: greedy (deterministic) on first try, sampling only on retry
gen_kwargs = {"temperature": 0.4, "top_p": 0.9, "top_k": 50} if is_retry else {"temperature": 0.0}
# Answer phrasing: always greedy
```
Why greedy first: it's deterministic, so if the first attempt has a bug, retrying with the *identical* prompt just reproduces the identical bug. Sampling is only useful on the retry pass, where you actually want a different attempt.

---

## 3. The two system prompts **[REUSE THE PATTERN, REWRITE THE CONTENT]**

Two separate prompts, one per LLM call — keeping them separate (rather than one prompt doing both jobs) means each stays focused and shorter.

### Query-writing prompt — template shape

```
You are a [DOMAIN] query-writing assistant for a [DATA DESCRIPTION].

Collection/table: [name]
Fields: [field]: [type] ([allowed values if enum]), [field]: [type], ...

Given a question, output ONLY a JSON object with this exact shape:
{"intent": "data_query", "pipeline": [ ...query stages... ]}

[WORKED EXAMPLE — one full input/output pair, using the exact query
language/dialect you're targeting (MongoDB aggregation, SQL, etc.)]

[LIST OF ALLOWED OPERATORS — be explicit; models hallucinate operator
names that sound plausible but don't exist]

Current date: [today's date]. Output ONLY the JSON object, nothing else.
```

**Retail example** (swap the factory-alert schema for a retail one):
```
You are a retail analytics query-writing assistant for an order/sales database.

Collection: orders
Fields: order_id (string), product_category (string: ELECTRONICS, APPAREL,
GROCERY, HOME_GOODS), order_total (number, currency), customer_id (string),
status (string: COMPLETED, RETURNED, CANCELLED), timestamp (date).

Given a question, output ONLY a JSON object with this exact shape:
{"intent": "data_query", "pipeline": [ ...MongoDB aggregation stages... ]}

Example:
Question: "how many orders were placed today"
Answer: {"intent": "data_query", "pipeline": [{"$match": {"status": {"$ne": "CANCELLED"}, "timestamp": {"$gte": "2026-08-21T00:00:00", "$lt": "2026-08-22T00:00:00"}}}, {"$count": "total"}]}

Current date: 2026-08-21. Output ONLY the JSON object, nothing else.
```

**Important, hard-won lesson**: this prompt will grow over time as testing surfaces specific model mistakes (ours ended up at ~2,347 tokens after a full project's worth of fixes). Budget for that growth when sizing context windows — don't assume the prompt stays small.

### Answer-phrasing prompt — template shape

```
You are a friendly, conversational [DOMAIN] data assistant — write like
you're briefing a colleague, not printing a report.

Given the question and the raw result rows below, write a natural
1-3 sentence answer using ONLY the numbers/values actually present in
the rows. Never invent a number. Never claim a row is the "highest"/
"most"/"least" unless the rows are actually sorted that way.
```

---

## 4. The reliability-engineering layer **[REUSE THE PATTERN — THIS IS THE MOST VALUABLE PART]**

This is the actual hard-won part of the project: a 3B-class model makes the same handful of mistake *categories* over and over, regardless of domain. None of these categories are alert-specific — they're generic LLM-writes-structured-query failure modes. Rebuild each of these for retail; the *logic* transfers, only the field names change.

| Repair category | What it catches | Reusable? |
|---|---|---|
| **JSON syntax repair** (missing brace, merged stages, trailing garbage) | Model produces almost-valid JSON with one small structural mistake | **Fully reusable, zero domain knowledge needed** |
| **Date-range override** | Model's own date arithmetic is unreliable ("yesterday," "last 7 days," specific dates) — a deterministic date-phrase parser computes the correct range and **overwrites** whatever the model wrote | Fully reusable if the new domain also has a timestamp field (retail does) |
| **"Needs aggregation" check** | Model returns a pipeline that only filters/limits instead of actually counting/grouping — a "which X had the most Y" question needs `$group`+`$sort`, not just `$match` | Fully reusable pattern; the *check* (does this question need aggregation) is generic |
| **"Needs type filter" check** | Question names a specific category (e.g. "hand touch alerts" / "electronics orders") but the pipeline never filters to it | Reusable pattern — swap `alert_type` for `product_category` (or whatever the domain's category field is) |
| **Wrong grouping-field check** | Model groups by the wrong dimension (e.g. calendar date instead of day-of-week, or vice versa) when the question asked for the other one | Fully reusable, domain-agnostic |
| **Nested-accumulator repair** | Model nests a `$sum`/`$avg` inside `_id` instead of as its sibling — breaks aggregation silently | Fully reusable, MongoDB-specific but domain-agnostic |
| **"Strip unwanted date filter"** | Model defaults to a recent-window filter even when the question has no time reference at all | Fully reusable |
| **Self-correction retry loop** | If the pipeline errors OR fails one of the above checks, regenerate once with the specific problem described back to the model | Fully reusable mechanism |
| **"Trusted pipeline" flag** | Deterministically-built pipelines (fast paths) skip the heuristic checks above entirely — a heuristic can incorrectly "fix" a pipeline that's already correct by construction | Fully reusable safety mechanism |

**The meta-lesson, more important than any individual check**: don't trust the model's prose instructions to reliably prevent a mistake. When testing surfaces a *specific, repeatable* failure, write a small deterministic function that detects and fixes that exact shape — prompt instructions are advisory, code that inspects and rewrites the output is not.

## 5. Deterministic fast paths **[REUSE THE PATTERN, REWRITE THE REGEXES]**

For a handful of extremely common question shapes, skip the LLM entirely and compute the query in code:

```python
def _try_fast_path(question: str) -> dict | None:
    if _EXCLUDE_RE.search(question):        # bail if question has any complexity keyword
        return None
    m = _TOTAL_COUNT_RE.match(question)      # e.g. "how many orders [were placed] [time phrase]"
    if not m:
        return None
    time_phrase = m.group(1).strip()
    match_cond = {"status": {"$ne": "CANCELLED"}}
    if time_phrase:
        date_range = _extract_relative_date_range(time_phrase, now)
        if date_range is None:
            return None   # unrecognized phrase — don't guess, let the LLM handle it
        match_cond["timestamp"] = {"$gte": date_range[0], "$lt": date_range[1]}
    return {"intent": "data_query", "pipeline": [{"$match": match_cond}, {"$count": "total"}], "_fast_path": True}
```

Design rule that matters: **be conservative**. Any complexity keyword (a specific category name, a comparison, a grouping request) should fall through to the full LLM path rather than being guessed at. A fast path should only ever add a shortcut, never remove a capability.

**Retail fast-path candidates** (the retail-equivalent of the highest-value ones we built): plain order counts over a time window, plain revenue totals over a time window, category-filtered counts. Build these first — they're usually >50% of real usage and get you 0ms, 100%-correct answers with zero LLM risk.

## 6. Deterministic date/time parsing **[REUSE NEARLY AS-IS]**

This one function alone prevented a large fraction of real bugs. It should be lifted close to verbatim into any new domain that has a timestamp field:

```python
def _extract_relative_date_range(question: str, now: datetime) -> tuple[datetime, datetime] | None:
    """Regex fallback for common relative-date phrasing — overrides the
    model's own date arithmetic, which is unreliable under greedy decoding."""
    # handles: absolute dates ("22nd july 2026", "july 22, 2026", ISO),
    # "last N minutes/hours/days/weeks/years", "yesterday", "today",
    # "this/last week", "this/last month", "this/last year", "quarter",
    # a bare year ("2023"), "month year" ("july 2026")
    ...
    return None  # unrecognized — don't guess
```

Hard-won details worth keeping: tolerate missing/extra whitespace and commas between date components (real users type "22ndjuly2026" and "28th July, 2026" both), and be careful that a bare-year fallback doesn't accidentally swallow a more specific phrase like "month year" or "quarter" — check the more specific patterns first.

## 7. What to build fresh for retail **[DOMAIN-SPECIFIC]**

- The actual schema (fields, types, enum values) for both prompts.
- The fast-path regexes (question phrasing is domain-specific — "how many orders" vs "how many alerts").
- The `filters_to_type`-equivalent check, using the retail category field.
- A retail-appropriate NORMAL_OPERATION-equivalent exclusion, if any (e.g. exclude `CANCELLED` orders from revenue totals by default, the way `NORMAL_OPERATION` alerts are excluded by default).
- Test questions and their expected pipelines (build a regression suite the same way — a fixed set of real questions checked against a live database, not mocked).

## 8. Regression testing **[REUSE AS-IS]**

Keep one file with a fixed list of representative questions and verifiers, checked against a live database (not mocked) after any prompt/logic change:

```python
TestCase("count_today", "counts", ["How many orders happened today?"],
         count_matches_db(lambda now: {"status": {"$ne": "CANCELLED"}, "timestamp": {"$gte": today_start(now)}}),
         [is_aggregated]),
```
Two kinds of check per case: **structural** (does the pipeline shape make sense — aggregates instead of dumping raw docs, filters to the category actually asked about) and **value** (does the number match an independent, direct database query — never trust the pipeline's own reported result blindly).

---

## Summary: what's actually new work for a new domain

Everything in sections 2, 4, 6, and 8 above should be reusable close to as-is. What genuinely needs to be written fresh is small: the schema description in both prompts, the fast-path regexes, and the category-filter checks. Budget most of the actual engineering time for the same process that built this system in the first place — **test real questions against the live system, read what the model actually produced, find the specific repeatable mistake, add a targeted deterministic fix** — not for writing new architecture from scratch.
