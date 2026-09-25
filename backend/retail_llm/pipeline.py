"""Orchestrator — mirrors the reference chat backend's flow, on SQLite.

    question (+ conversation history)
      -> spelling correction (domain vocabulary)
      -> classify intent (greeting / unsupported / data_query)
      -> LLM #1 writes SQL -> validate/repair -> run
             -> self-correction check fails? regenerate once, re-run
      -> deterministic maths layer (percentage / margin / growth / average)
      -> LLM #2 phrases rows, or the maths layer's ready-made sentence
      -> answer (+ per-stage lifecycle timings)
"""
import json
import re
import time

from . import llm
from . import maths
from . import phrase as _phrase
from . import report
from . import spellcheck
from . import tools
from .config import now
from .db import run_readonly
from .dates import extract_range, label
from .repair import ValidationError, diagnose, fix_ambiguous_bill_sql, fix_undefined_alias, validate_sql
from .schema_prompt import answer_prompt, query_prompt


class QueryError(Exception):
    pass


GREETING_RE = re.compile(
    r"^\s*(hi|hey|hello|yo|hiya|greetings|good\s+(morning|afternoon|evening)|how'?s it going|"
    r"thanks|thank you|thankyou|ty|cheers|namaste|bye|goodbye|who are you|what can you do)\b", re.I)
GREETING_ANSWER = ("Hi! Ask me about stock levels, billing and revenue, sales trends, "
                   "customers, footfall or staff performance.")
UNSUPPORTED_ANSWER = ("I can only answer questions about this store's retail data — "
                      "stock, bills, revenue, products, customers, footfall and staff.")

_RETAIL_HINT = re.compile(
    r"stock|inventory|item|product|sku|revenue|sales|sale|sell|sold|bill|invoice|receipt|"
    r"cashier|counter|checkout|customer|shopper|footfall|foot\s*traffic|visitor|discount|"
    r"exception|void|reorder|threshold|categor|price|pricing|cost|units|qty|quantity|"
    r"transaction|order|staff|employee|worker|manager|roster|on\s*duty|team|"
    r"store|shop|branch|dead\s*stock|slow\s*mov|fast\s*mov|"
    r"margin|profit|turnover|basket|spend|spent|purchase|buy|bought|today|yesterday|"
    r"week|month|quarter|year|hour|peak|busiest|top\s*\d|below|threshold", re.I)

# general-knowledge / chit-chat that is clearly not about the store's data
_OFF_TOPIC = re.compile(
    r"^\s*(what|who|when|where|why|how)\s+(is|are|was|were|does|do|did|can|would|will)\b"
    r"(?!.*\b(my|our|the store|this (store|shop|month|week|branch)|revenue|sales|stock|"
    r"bill|customer|cashier|footfall|category|product|staff)\b)", re.I)


def _classify(question: str, history: list | None = None) -> str:
    q = question.strip()
    words = re.findall(r"[A-Za-z]{2,}", q)
    if GREETING_RE.match(q) and len(words) <= 5:
        return "greeting"
    has_hint = bool(_RETAIL_HINT.search(q))

    # a follow-up right after a real data question ("name those 8", "and last
    # week?", "just the top 3") — the pronoun/ellipsis carries the context, so
    # short and hint-free is expected. Trust it unless it's a clear greeting.
    in_thread = bool(history) and any(t.get("sql") for t in history[-3:])
    if in_thread and words and not _OFF_TOPIC.match(q):
        return "data_query"

    if has_hint:
        return "data_query"
    if len(words) < 2:
        return "unsupported"
    # "what is football?", "who is the president?", "how does gravity work" —
    # a wh-question with no retail term anywhere is not about our data
    if _OFF_TOPIC.match(q):
        return "unsupported"
    if len(words) < 5:
        return "unsupported"
    return "data_query"


def _stage(stages, name, description, t0):
    stages.append({"name": name, "description": description,
                   "duration_ms": round((time.time() - t0) * 1000)})


# --------------------------------------------------------------------------
# query generation
# --------------------------------------------------------------------------
_FIELD_RE = re.compile(r'"(sql|tool|query)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _extract_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise QueryError("model did not return JSON")
    text = re.sub(r",(\s*[}\]])", r"\1", m.group(0))
    text = re.sub(r"//[^\n]*", "", text)
    for candidate in (text, text.replace("\n", " ")):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    # A longer SQL (e.g. two subqueries for a period comparison) sometimes
    # trips the model into appending stray characters right before the
    # closing brace -- the JSON as a whole won't parse, but the fields
    # themselves are still intact double-quoted strings. Recover those
    # directly rather than discarding a perfectly good query.
    fields = dict(_FIELD_RE.findall(m.group(0)))
    if "sql" in fields or "tool" in fields:
        return {k: json.loads(f'"{v}"') for k, v in fields.items()}
    raise QueryError("bad JSON from model")


def _history_block(history) -> str:
    if not history:
        return ""
    lines = ["\nRecent conversation (for follow-up context only):"]
    for turn in history[-4:]:
        lines.append(f"  Q: {turn.get('question', '')}")
        if turn.get("sql"):
            lines.append(f"  SQL: {turn['sql']}")
        lines.append(f"  A: {turn.get('answer', '')}")
    return "\n".join(lines)


def _product_link_block(question) -> str:
    matches = tools.link_products_in_question(question)
    if not matches:
        return ""
    lines = ["\nProduct(s) the question names (filter by product_id — these are exact):"]
    for r in matches:
        lines.append(f"  product_id {r['product_id']}: \"{r['name']}\" | {r['category']} "
                     f"| price {r['price']} | stock {r['current_stock']}")
    return "\n".join(lines)


_CONTINUATION_RE = re.compile(
    r"\bbreakdown\b|\bbreak down\b|\bsplit\b|\bby category\b|\bper category\b|"
    r"\beach categor|\btop\b|\bwhich\b|\bcompare\b|\bacross\b", re.I)


def _inherited_range(question, history, now_dt):
    """A follow-up like 'breakdown by category' right after 'sales yesterday'
    should still mean yesterday -- but only carry the prior turn's date scope
    when the new question looks like an aggregate continuation (not a
    lookup for something else, like a product price) and states no period of
    its own."""
    if not history or not _CONTINUATION_RE.search(question):
        return None
    prior_q = history[-1].get("question", "")
    return extract_range(prior_q, now_dt)


_COMPARE_SPLIT_RE = re.compile(r"\s+(?:vs\.?|versus|compared to|against)\s+", re.I)


def _compare_ranges(question, now_dt):
    """For an explicit 'X vs Y' period comparison, compute BOTH ranges
    deterministically instead of leaving the second one for the model to
    work out itself -- a small model doesn't reliably compute "the period
    immediately before this one" (e.g. it has computed a whole month back
    for "last week", instead of the 7 days before this week)."""
    parts = _COMPARE_SPLIT_RE.split(question, maxsplit=1)
    if len(parts) != 2:
        return None
    first = extract_range(parts[0], now_dt)
    second = extract_range(parts[1], now_dt)
    if first and second:
        return first, second
    return None


def _user_prompt(question, history, problem):
    from .config import LLM_BACKEND
    compact = LLM_BACKEND == "axelera"   # tight 1024-ctx build — keep it lean

    parts = [f"Question: {question}"]
    cmp_ranges = _compare_ranges(question, now())
    if cmp_ranges:
        first, second = cmp_ranges
        parts.append(f"Interpreted date ranges for this comparison — first = {label(first)}; "
                     f"second = {label(second)}. Use each verbatim in its own subquery/branch; "
                     "do not compute either one yourself.")
    else:
        rng = extract_range(question, now())
        inherited = False
        if not rng:
            rng = _inherited_range(question, history, now())
            inherited = rng is not None
        if rng:
            tag = " (carried over from the previous question — the follow-up names no period of its own)" if inherited else ""
            parts.append(f"Interpreted date range: {label(rng)}{tag}")
    parts.append(f"Current date: {now().isoformat()}")
    pl = _product_link_block(question)
    if pl:
        parts.append(pl)
    # History text degrades the tight-context Metis builds (guide §4); the
    # deterministic layers (bill-ref resolver, product linker, classify)
    # already carry follow-ups. Only include it on the roomy dev backend.
    if not compact:
        hb = _history_block(history)
        if hb:
            parts.append(hb)
    if problem:
        parts.append(f"\nPrevious attempt was wrong: {problem}\nWrite a corrected query.")
    return "\n".join(parts)


_SQL_RE = re.compile(r"(SELECT\b.*?)(;|\Z)", re.S | re.I)


def _extract_sql(text: str) -> str:
    """Pull a bare SELECT out of raw model text (Axelera path — the compact
    prompt asks for SQL, not JSON)."""
    text = re.sub(r"```(?:sql)?", "", text)
    m = _SQL_RE.search(text)
    if not m:
        raise QueryError("model did not return a SELECT")
    from .repair import canonicalize_enums
    return canonicalize_enums(m.group(1).strip().rstrip(";"))


def _gen_sql(question, history, problem, is_retry, stages=None) -> str:
    """One query-generation turn.

    - Axelera (compact, tight-ctx): the model emits raw SQL; extract + canonicalise.
    - llama.cpp (dev): JSON, with an inner find_product tool loop.
    """
    from .config import LLM_BACKEND
    user = _user_prompt(question, history, problem)

    if LLM_BACKEND == "axelera":
        raw = llm.complete(query_prompt(), user, is_retry=is_retry, max_tokens=420)
        return _extract_sql(raw)

    for step in range(2):
        raw = llm.complete(query_prompt(), user, is_retry=is_retry, max_tokens=420)
        obj = _extract_json(raw)

        if obj.get("tool") == "find_product" and step == 0:
            t0 = time.time()
            result = tools.find_product(str(obj.get("query", "")).strip())
            if stages is not None:
                n = len(result.get("matches", []))
                _stage(stages, "Tool · find_product",
                       f"Resolved \"{obj.get('query')}\" to {n} catalogue product(s).", t0)
            user = (user + f"\n\nYou called: {json.dumps(obj)}\n"
                    + tools.format_tool_result(result))
            continue

        sql = obj.get("sql") or ""
        if not sql:
            raise QueryError("no 'sql' field in model output")
        from .repair import canonicalize_enums
        return canonicalize_enums(sql)
    raise QueryError("model kept calling tools instead of writing SQL")


def _plan(question, history, stages):
    """Return {sql, params, explanation, source}. Appends a stage."""
    t0 = time.time()

    if not llm.available():
        from .config import LLM_BACKEND
        st = {}
        try:
            st = llm.status()
        except Exception:
            pass
        if LLM_BACKEND == "axelera":
            why = st.get("error")
            if why and "DDR memory" in why:
                msg = ("The Metis card has no free memory, so the AI model can't load — "
                       "the card needs a reset (axdevice --reload-firmware, or reboot the host).")
            elif why:
                msg = f"The AI model isn't available on the Metis card ({why})."
            else:
                msg = "The AI model is still loading on the Metis card — try again in a moment."
        else:
            msg = ("The local model isn't loaded. Set RETAIL_GGUF_MODEL_PATH / install "
                   "llama-cpp-python, or try again once it's warmed up.")
        raise QueryError(msg)

    has_range = bool(_compare_ranges(question, now()) or extract_range(question, now()) or _inherited_range(question, history, now()))
    problem = None
    for attempt in range(2):
        try:
            raw_sql = _gen_sql(question, history, problem, is_retry=(attempt == 1), stages=stages)
            raw_sql, link_note = tools.repair_product_literals(raw_sql)
            raw_sql = fix_ambiguous_bill_sql(question, raw_sql)
            raw_sql = fix_undefined_alias(raw_sql)
            sql = validate_sql(raw_sql)
        except (QueryError, ValidationError) as e:
            problem = f"the query was invalid ({e})"
            continue
        pre = diagnose(question, sql, has_range=has_range)
        if pre and attempt == 0:
            problem = pre
            continue
        _stage(stages, "Query generation",
               "Asked the language model to translate the question into a SQL SELECT "
               "(with JSON repair, SELECT-only validation and a row cap applied).", t0)
        if link_note:
            _stage(stages, "Value linking (find_product)",
                   f"Corrected the product the model named: {link_note}.", t0)
        return {"sql": sql, "params": [], "explanation": "",
                "source": "llm" + ("_retry" if attempt else "")}

    raise QueryError("could not produce a valid query after a retry")


def _resolve(question, history):
    """-> (plan, rows, stages, intent)."""
    stages = []
    t0 = time.time()
    corrected = spellcheck.correct(question)
    if corrected != question:
        _stage(stages, "Spelling correction",
               f'Interpreted "{question}" as "{corrected}".', t0)
        question = corrected
    intent = _classify(question, history)
    _stage(stages, "Understanding the question",
           f"Classified the message as '{intent}'.", t0)
    if intent != "data_query":
        return None, [], stages, intent

    if report.is_report_request(question):
        t0 = time.time()
        rep = report.build(question, now())
        _stage(stages, "Report generation",
               "Ran several deterministic SQL queries (revenue, top categories/products/"
               "cashiers, peak day/hour, exceptions, footfall) and composed a report — "
               "no model call needed.", t0)
        plan = {"sql": rep["sql"], "params": [], "source": rep["source"], "explanation": "",
                "computed": {"sentence": rep["answer"], "note": "report composed deterministically"}}
        return plan, rep["result"], stages, intent

    plan = _plan(question, history, stages)

    t0 = time.time()
    try:
        rows = run_readonly(plan["sql"], tuple(plan.get("params", ())))
    except Exception as e:
        rows = None
        exec_err = str(e)
    else:
        exec_err = None
    _stage(stages, "Database execution", "Ran the query against the SQLite database.", t0)

    # self-correction: one regeneration if it errored or failed a check
    has_range = bool(_compare_ranges(question, now()) or extract_range(question, now()) or _inherited_range(question, history, now()))
    for attempt in range(2):
        problem = None
        if exec_err:
            problem = f"the database rejected the query ({exec_err})"
        else:
            problem = diagnose(question, plan["sql"], has_range=has_range)
        if not problem:
            break
        try:
            t0 = time.time()
            new_sql = _gen_sql(question, history, problem, is_retry=True, stages=stages)
            new_sql, _ = tools.repair_product_literals(new_sql)
            new_sql = fix_ambiguous_bill_sql(question, new_sql)
            new_sql = fix_undefined_alias(new_sql)
            new_sql = validate_sql(new_sql)
            new_rows = run_readonly(new_sql, ())
        except Exception:
            _stage(stages, f"Self-correction attempt {attempt + 1}",
                   f"Previous query had a problem ({problem}); regeneration did not yield a usable fix, kept the prior result.", t0)
            break
        plan = {**plan, "sql": new_sql, "source": plan["source"] + "+corrected"}
        rows, exec_err = new_rows, None
        _stage(stages, f"Self-correction attempt {attempt + 1}",
               f"Previous query had a problem ({problem}); regenerated and re-ran it.", t0)

    if rows is None:
        raise QueryError(f"query failed to execute: {exec_err}\nSQL: {plan['sql']}")

    # deterministic maths layer: percentages, shares, margins, growth rates and
    # per-unit averages are computed here in exact Python arithmetic from the
    # raw component numbers already in `rows` — never left to the model.
    rows, computed = maths.augment(rows, question)
    if computed:
        plan["computed"] = computed
        _stage(stages, "Maths layer", computed["note"], time.time())

    return plan, rows, stages, intent


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def _meta(question, intent, plan, rows, stages):
    return {
        "question": question,
        "intent": intent,
        "sql": plan["sql"] if plan else None,
        "params": plan.get("params", []) if plan else [],
        "source": plan["source"] if plan else None,
        "explanation": plan.get("explanation", "") if plan else "",
        "result": rows[:200],
        "row_count": len(rows),
        "stages": stages,
    }


def answer(question: str, history: list | None = None) -> dict:
    plan, rows, stages, intent = _resolve(question, history)
    if intent == "greeting":
        return {**_meta(question, intent, None, [], stages), "answer": GREETING_ANSWER}
    if intent == "unsupported":
        return {**_meta(question, intent, None, [], stages), "answer": UNSUPPORTED_ANSWER}
    if plan and plan.get("computed"):
        return {**_meta(question, intent, plan, rows, stages), "answer": plan["computed"]["sentence"]}

    t0 = time.time()
    if _use_deterministic_phrasing(plan, rows, question):
        ans = _phrase.rows_to_sentence(rows, question)
        _stage(stages, "Answer generation",
               "Phrased directly from the result rows in code — no model call needed.", t0)
    else:
        raw = ""
        try:
            raw = llm.complete(answer_prompt(), _phrase_prompt(question, rows), max_tokens=220)
        except Exception:
            pass
        ans = _phrase.finalize(rows, raw, question)
        _stage(stages, "Answer generation",
               "Asked the model to phrase the real result rows as a natural answer, then ran safety "
               "checks (no invented or dropped values, no false 'no data').", t0)
    return {**_meta(question, intent, plan, rows, stages), "answer": ans}


def answer_stream(question: str, history: list | None = None):
    """Yields (event, payload): 'meta' once the query has run, 'token' per
    answer chunk, 'done' with the final safety-checked answer."""
    plan, rows, stages, intent = _resolve(question, history)
    meta = _meta(question, intent, plan, rows, stages)
    yield "meta", meta

    if intent == "greeting":
        yield "done", {"answer": GREETING_ANSWER, "stages": stages}
        return
    if intent == "unsupported":
        yield "done", {"answer": UNSUPPORTED_ANSWER, "stages": stages}
        return
    if plan and plan.get("computed"):
        ans = plan["computed"]["sentence"]
        yield "token", {"text": ans}
        yield "done", {"answer": ans, "stages": stages}
        return
    if not rows:
        yield "done", {"answer": "Nothing matched that query.", "stages": stages}
        return

    t0 = time.time()
    if _use_deterministic_phrasing(plan, rows, question):
        ans = _phrase.rows_to_sentence(rows, question)
        _stage(stages, "Answer generation",
               "Phrased directly from the result rows in code — no model call needed.", t0)
        yield "token", {"text": ans}
        yield "done", {"answer": ans, "stages": stages}
        return

    full = ""
    held = ""
    committed = suppressed = False
    try:
        for chunk in llm.complete_stream(answer_prompt(), _phrase_prompt(question, rows), max_tokens=220):
            full += chunk
            if committed:
                yield "token", {"text": chunk}
                continue
            if suppressed:
                continue
            held += chunk
            if len(held) >= 20:
                if _phrase._NO_DATA_RE.search(held):
                    suppressed = True
                else:
                    yield "token", {"text": held}
                    committed = True
    except Exception:
        pass
    if not committed and not suppressed and held and not _phrase._NO_DATA_RE.search(held):
        yield "token", {"text": held}

    final = _phrase.finalize(rows, full.strip(), question)
    _stage(stages, "Answer generation",
           "Asked the model to phrase the rows (streamed), then ran deterministic safety checks.", t0)
    yield "done", {"answer": final, "stages": stages}


def _use_deterministic_phrasing(plan, rows, question) -> bool:
    """The model phrases anything small enough to summarise cleanly (a total,
    a single entity, a short top-N) — that reads far better than a template.
    A few question shapes are still phrased deterministically because a small
    model reliably gets them wrong."""
    if not llm.available():
        return True
    # "do we sell X" (yes/no the model gets backwards), "how many types of X"
    # (answer is the row count), and "this period vs previous" (model reports
    # only one side) — phrase all deterministically.
    if _phrase._YESNO_RE.match(question or "") or _phrase._HOWMANY_TYPES_RE.search(question or ""):
        return True
    if rows and len(rows) == 2 and any("period" in str(k).lower() for k in rows[0]):
        return True
    # A "top N" list (e.g. 10 rows) asks the model to enumerate more items
    # than its ~220-token answer budget reliably fits in flowing prose -- it
    # either truncates mid-list or contradicts its own instruction to name
    # just the top few. The table is shown in full separately regardless, so
    # a longer result is always safer phrased deterministically.
    from .config import LLM_BACKEND
    cap = 6 if LLM_BACKEND == "axelera" else 8
    if rows and len(rows) > cap:
        return True
    return False


def _phrase_prompt(question, rows):
    from .config import LLM_BACKEND
    if LLM_BACKEND == "axelera":
        # tight ctx + a 3B model that mistakes "(1 total)" framing for the
        # answer — give it just the rows, no row-count preamble.
        preview = _phrase.preformat_for_prompt(rows[:8])
        body = json.dumps(preview[0] if len(preview) == 1 else preview, default=str)
        return (f"Question: {question}\n"
                f"Query result: {body}\n"
                f"Answer in ONE sentence, stating the value(s) from the result.")
    preview = _phrase.preformat_for_prompt(rows[:40])
    return (f"Question: {question}\n\n"
            f"Result rows ({len(rows)} total, showing {len(preview)}):\n"
            f"{json.dumps(preview, default=str, indent=1)}\n\n"
            f"Answer the question in 1-3 sentences.")


# back-compat: some callers/tests use plan()
def plan(question: str, history: list | None = None) -> dict:
    stages = []
    return _plan(question, history, stages)
