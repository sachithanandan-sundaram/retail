"""Regression suite — representative questions checked against the LIVE db.

There are no fast-path shortcuts anymore: every data question goes through
the model (query-writing + answer-phrasing) plus the deterministic repair /
maths / spellcheck layers around it. Two kinds of check:
  deterministic — repair.py, maths.py, spellcheck.py, tools.py: pure Python,
                  no model needed, always run.
  end-to-end    — a real question through retail_llm.pipeline.answer(); these
                  are skipped when the local model isn't loaded (CI without a
                  GPU/model), since fast paths no longer provide a model-free
                  path to a SQL result.

Run:  RETAIL_NOW=2026-08-27T15:00:00  python -m pytest -q
"""
import os
import re

os.environ.setdefault("RETAIL_NOW", "2026-08-27T20:00:00")

import pytest

from retail_llm import llm
from retail_llm.config import DB_PATH, now
from retail_llm.db import run_readonly
from retail_llm.dates import extract_range
from retail_llm.pipeline import answer, plan
from retail_llm.repair import diagnose, needs_aggregation, validate_sql

pytestmark = pytest.mark.skipif(not DB_PATH.exists(),
                                reason="run `python -m retail_llm.generate_data` first")

needs_llm = pytest.mark.skipif(not llm.available(),
                               reason="local model isn't loaded — set RETAIL_GGUF_MODEL_PATH")


def _one(sql, params=()):
    rows = run_readonly(sql, params)
    return rows[0] if rows else {}


# --------------------------------------------------------------------------
# end-to-end: every confirmed demo question must produce a real SQL answer
# --------------------------------------------------------------------------
DEMO_QUESTIONS = [
    "What items are below threshold right now?",
    "Show me stock items sitting unsold for more than 30 days",
    "What was total revenue today?",
    "What was total revenue this week?",
    "List the top 10 highest-value bills this month",
    "How much has Priya billed this week?",
    "Which counter person has billed the most this week?",
    "What's the average bill value this month?",
    "Show average billing time for each cashier",
    "List all billing exceptions from today",
    "What are the top 10 fastest-moving items this week?",
    "Which items sold the most units in the last 7 days?",
    "Which items haven't sold in the last 30 days?",
    "What day of the week has peak sales?",
    "What time of day has peak sales?",
    "What's the average revenue per customer this week?",
    "Show me everything about customer #1234",
    "Which employee handled the most customers?",
    "What was today's total footfall?",
    "What were the busiest hours this month?",
]


@needs_llm
@pytest.mark.parametrize("q", DEMO_QUESTIONS)
def test_demo_question_answers(q):
    p = plan(q)
    assert p["sql"]
    rows = run_readonly(p["sql"], tuple(p.get("params", ())))
    assert isinstance(rows, list)


# --------------------------------------------------------------------------
# value checks — the model's SQL result matches an independent direct query
# --------------------------------------------------------------------------
@needs_llm
def test_revenue_today_matches_db():
    a, b = extract_range("today", now())
    p = plan("What was total revenue today?")
    got = run_readonly(p["sql"], tuple(p.get("params", ())))[0]
    exp = _one("SELECT ROUND(SUM(total_amount),2) r, COUNT(*) c FROM transactions "
               "WHERE ts >= ? AND ts < ?", (a.isoformat(), b.isoformat()))
    got_revenue = next(v for k, v in got.items() if "revenue" in k.lower())
    assert got_revenue == exp["r"]


@needs_llm
def test_below_threshold_matches_db():
    p = plan("What items are below threshold right now?")
    got = run_readonly(p["sql"], tuple(p.get("params", ())))
    exp = _one("SELECT COUNT(*) c FROM products WHERE current_stock < reorder_threshold")
    assert len(got) == exp["c"]


@needs_llm
def test_footfall_today_matches_db():
    a, b = extract_range("today", now())
    p = plan("What was today's total footfall?")
    got = run_readonly(p["sql"], tuple(p.get("params", ())))[0]
    exp = _one("SELECT SUM(count) s FROM footfall WHERE ts>=? AND ts<?",
               (a.isoformat(), b.isoformat()))
    got_val = next(v for v in got.values() if isinstance(v, (int, float)))
    assert got_val == exp["s"]


@needs_llm
def test_customer_lookup_returns_one_row():
    p = plan("Show me everything about customer #1234")
    assert "customers" in p["sql"].lower()
    rows = run_readonly(p["sql"], tuple(p.get("params", ())))
    exp = _one("SELECT name FROM customers WHERE customer_id = 1234")
    assert len(rows) == 1 and exp["name"] in rows[0].values()


@needs_llm
def test_ambiguous_show_bill_resolves_to_one_bill():
    # regression: "show bill" with no number used to produce SQL with no
    # transaction_id filter at all, and phrase.py then merged line items from
    # many different bills under one fake header/total.
    r = answer("show bill")
    assert r["row_count"] > 0
    distinct = {row.get("bill_no") for row in r["result"]}
    assert len(distinct) == 1
    exp = _one("SELECT MAX(transaction_id) m FROM transactions")["m"]
    assert exp in distinct


@needs_llm
def test_bill_lookup_returns_all_line_items():
    p = plan("show me what was in bill #15000")
    rows = run_readonly(p["sql"], tuple(p.get("params", ())))
    n_items = _one("SELECT COUNT(*) c FROM transaction_items WHERE transaction_id=15000")["c"]
    assert len(rows) == n_items and n_items > 0


# --------------------------------------------------------------------------
# repair / validation layer (deterministic, no LLM needed)
# --------------------------------------------------------------------------
def test_validate_rejects_writes():
    for bad in ["DELETE FROM products", "SELECT 1; DROP TABLE staff",
                "UPDATE products SET current_stock = 0", "INSERT INTO staff VALUES (1)"]:
        with pytest.raises(Exception):
            validate_sql(bad)


def test_validate_enforces_limit():
    out = validate_sql("SELECT * FROM products")
    assert re.search(r"limit\s+\d+", out, re.I)
    out2 = validate_sql("SELECT * FROM products LIMIT 99999")
    assert "LIMIT 500" in out2


def test_needs_aggregation_flags_bare_filter():
    assert needs_aggregation("how many bills today?",
                             "SELECT * FROM transactions WHERE ts >= '2026-08-27'")
    assert not needs_aggregation("how many bills today?",
                                 "SELECT COUNT(*) FROM transactions")


def test_repair_product_id_literal_fixes_copied_example_id():
    # regression: "Price of pro paneer 200g" -> the model copied product_id
    # 214 straight out of schema_prompt.py's worked example instead of using
    # the real id (30, "Select Paneer 200g") that link_products_in_question
    # actually resolved for this question.
    from retail_llm.tools import repair_product_id_literal
    bad = "SELECT name, price, current_stock FROM products WHERE product_id = 214\nLIMIT 500"
    fixed, note = repair_product_id_literal(bad, "Price of pro paneer 200g")
    assert "product_id = 30" in fixed and note
    rows = run_readonly(fixed)
    assert rows and "Paneer" in rows[0]["name"]
    # a JOIN query is left alone -- product_id there might not even mean "the
    # product this question names" (e.g. a correlated subquery over a
    # different table's rows)
    joined = ("SELECT p.name FROM products p JOIN transaction_items ti "
              "ON ti.product_id = p.product_id WHERE p.product_id = 214")
    assert repair_product_id_literal(joined, "Price of pro paneer 200g") == (joined, None)


def test_fix_undefined_alias_repairs_single_table_query():
    from retail_llm.repair import fix_undefined_alias
    bad = "SELECT p.name, p.price, p.current_stock FROM products WHERE product_id = 30"
    fixed = fix_undefined_alias(bad)
    assert "FROM products p" in fixed
    rows = run_readonly(fixed)
    assert isinstance(rows, list)
    # already-correct SQL is left untouched
    good = "SELECT name, price FROM products WHERE product_id = 30"
    assert fix_undefined_alias(good) == good
    # a JOIN query is left alone -- too ambiguous which table needs the alias
    joined = ("SELECT p.name FROM products p JOIN transaction_items ti "
              "ON ti.product_id = p.product_id")
    assert fix_undefined_alias(joined) == joined


def test_ambiguous_bill_sql_gets_transaction_id_filter():
    from retail_llm.repair import ambiguous_single_bill_request, fix_ambiguous_bill_sql
    bad_sql = ("SELECT t.transaction_id AS bill_no, p.name AS item FROM transactions t "
               "JOIN transaction_items ti ON ti.transaction_id = t.transaction_id "
               "JOIN products p ON p.product_id = ti.product_id ORDER BY t.ts DESC LIMIT 500")
    assert ambiguous_single_bill_request("show bill", bad_sql)
    fixed = fix_ambiguous_bill_sql("show bill", bad_sql)
    assert "MAX(transaction_id)" in fixed
    assert ambiguous_single_bill_request("show bill", fixed) is None
    # an explicit number, or a "last/that" referent, is never touched
    assert ambiguous_single_bill_request("show bill #15000", bad_sql) is None
    assert ambiguous_single_bill_request("show the last bill", bad_sql) is None


def test_phrase_rejects_mixed_bill_numbers():
    from retail_llm.phrase import rows_to_sentence
    rows = [
        {"bill_no": 1, "item": "A", "quantity": 1, "line_total": 10, "total_amount": 10},
        {"bill_no": 2, "item": "B", "quantity": 1, "line_total": 20, "total_amount": 20},
    ]
    ans = rows_to_sentence(rows, "show bill")
    assert "Bill #1 —" not in ans  # must not fabricate a single-bill narrative


def test_units_sold_not_formatted_as_money():
    # regression: "total_sold" (a unit count) was rendered "Rs 51" because
    # "total" alone maps to money and nothing overrode it for a *_sold column.
    from retail_llm.phrase import _is_money_key
    assert not _is_money_key("total_sold")
    assert not _is_money_key("units_sold")
    assert _is_money_key("total_amount")  # a real money field must stay money


def test_diagnose_catches_category_on_wrong_table():
    from retail_llm.repair import wrong_category_source
    bad = "SELECT category, SUM(total_amount) FROM transactions GROUP BY category"
    assert wrong_category_source("breakdown by category", bad)
    good = ("SELECT p.category, SUM(ti.line_total) FROM transaction_items ti "
            "JOIN transactions t ON t.transaction_id = ti.transaction_id "
            "JOIN products p ON p.product_id = ti.product_id GROUP BY p.category")
    assert wrong_category_source("breakdown by category", good) is None
    assert wrong_category_source("what was total revenue today", bad) is None


def test_diagnose_catches_missing_category():
    d = diagnose("how many Beverages did we sell",
                 "SELECT COUNT(*) FROM transaction_items")
    assert d and "Beverages" in d


def test_find_product_tool_resolves_typos_and_partials():
    from retail_llm.tools import find_product, repair_product_literals
    paneer = _one("SELECT name FROM products WHERE name LIKE '%Paneer%' LIMIT 1").get("name")
    assert paneer
    assert any("Basmati Rice" in m["name"] for m in find_product("basmati rice")["matches"])
    names = [m["name"] for m in find_product("panner 200g")["matches"]]
    assert paneer in names
    assert find_product("xyzzy widget")["matches"] == []
    sql, note = repair_product_literals(
        "SELECT price FROM products WHERE name LIKE '%panner 200g%'")
    assert paneer in sql and note
    real = _one("SELECT name FROM products LIMIT 1")["name"]
    sql2, note2 = repair_product_literals(
        f"SELECT price FROM products WHERE name = '{real}'")
    assert note2 is None and sql2.strip().endswith(f"'{real}'")


# --------------------------------------------------------------------------
# maths layer (deterministic, no LLM needed)
# --------------------------------------------------------------------------
from retail_llm import maths


def test_maths_share_of_total():
    rows, c = maths.augment(
        [{"category_revenue": 45000.0, "total_revenue": 300000.0}],
        "what percentage of revenue came from Electronics")
    assert c and "15.0%" in c["sentence"]


def test_maths_margin():
    rows, c = maths.augment(
        [{"name": "Widget", "price": 500.0, "cost": 350.0}],
        "what is the profit margin on Widget")
    assert c and "30.0%" in c["sentence"]


def test_maths_growth():
    rows, c = maths.augment(
        [{"revenue": 120000.0}, {"revenue": 100000.0}],
        "how did revenue change this week vs last week")
    assert c and "up 20%" in c["sentence"]


def test_maths_average_per_unit():
    rows, c = maths.augment(
        [{"total_items": 450, "bill_count": 90}],
        "what is the average items per bill this week")
    assert c and "5" in c["sentence"]


def test_maths_ignores_unrelated_questions():
    rows, c = maths.augment([{"revenue": 1000.0, "bill_count": 5}], "what was total revenue today")
    assert c is None


# --------------------------------------------------------------------------
# spellcheck layer (deterministic, no LLM needed)
# --------------------------------------------------------------------------
from retail_llm import spellcheck


def test_spellcheck_fixes_domain_typo():
    assert spellcheck.correct("what was the revenu today") == "what was the revenue today"
    assert spellcheck.correct("show me cashiar performance") == "show me cashier performance"


def test_spellcheck_leaves_correct_text_alone():
    q = "What was total revenue today?"
    assert spellcheck.correct(q) == q


def test_spellcheck_leaves_product_names_and_common_words_alone():
    # short/common words and non-domain vocabulary must never be touched
    q = "what is the cheapest item in stock"
    assert spellcheck.correct(q) == q


# --------------------------------------------------------------------------
# report layer (deterministic, no LLM needed -- multi-query sales reports)
# --------------------------------------------------------------------------
from retail_llm import report


def test_report_detects_all_period_kinds():
    assert report._kind("monthly sales report") == "month"
    assert report._kind("weekly report") == "week"
    assert report._kind("quarterly report") == "quarter"
    assert report._kind("yearly report") == "year"
    assert report._kind("daily report") == "day"
    assert not report.is_report_request("what was total revenue today")


def test_report_builds_facets_and_summary():
    rep = report.build("give a monthly sales report for the month of august 2026", now())
    assert rep is not None
    assert "August 2026" in rep["answer"]
    assert "Rs" in rep["answer"]
    facets = rep["result"][0]
    for key in ("top_categories", "top_products", "top_cashiers",
                "revenue_by_weekday", "revenue_by_hour"):
        assert key in facets and isinstance(facets[key], list)
    exp = _one("SELECT ROUND(SUM(total_amount),2) r, COUNT(*) c FROM transactions "
               "WHERE ts >= '2026-08-01T00:00:00' AND ts < '2026-09-01T00:00:00'")
    assert str(exp["c"]) in rep["answer"].replace(",", "")


def test_report_works_without_llm():
    # a report is pure SQL + Python, so it must not require llm.available()
    r = answer("give me a yearly sales report")
    assert r["source"] == "report"
    assert r["result"][0]["top_categories"]


# --------------------------------------------------------------------------
# pipeline: intents, stages, answer shape
# --------------------------------------------------------------------------
def test_greeting_intent_short_circuits():
    r = answer("hi there")
    assert r["intent"] == "greeting"
    assert r["sql"] is None
    assert [s["name"] for s in r["stages"]] == ["Understanding the question"]


def test_unsupported_intent():
    assert answer("asdkjhqwe")["intent"] == "unsupported"


@needs_llm
def test_long_result_list_is_phrased_deterministically_never_truncated():
    # regression: a "top 10" list asked the model to enumerate more items
    # than its answer token budget reliably fits, and it truncated mid-list
    # ("...and 3." with nothing after). Any result over the row cap must be
    # phrased in code, not by the model, regardless of the question wording.
    r = answer("Top 10 fastest-moving items this week")
    assert r["row_count"] == 10
    assert r["answer"].rstrip().endswith(".")  # ends with real punctuation
    assert not re.search(r"\b\d+\.\s*$", r["answer"])  # never dangles on a bare "N."
    assert "Top" in r["answer"] and "of 10" in r["answer"]


@needs_llm
def test_data_query_answer_has_stages_and_matches_db():
    r = answer("What was total revenue today?")
    assert r["intent"] == "data_query"
    names = [s["name"] for s in r["stages"]]
    assert "Query generation" in names and "Database execution" in names and "Answer generation" in names
    assert len(r["answer"].split()) >= 3


@needs_llm
def test_answer_stream_emits_meta_and_done():
    from retail_llm.pipeline import answer_stream
    events = [ev for ev, _ in answer_stream("What was total revenue today?")]
    assert events[0] == "meta" and events[-1] == "done"


@needs_llm
def test_typo_question_still_answers():
    r = answer("what was the revenu today")
    assert r["intent"] == "data_query"
    assert r["sql"]


def test_extract_json_recovers_from_stray_trailing_characters():
    # regression: a longer SQL (two subqueries) sometimes trips the model into
    # appending a stray char right before the closing brace, e.g.
    # `...AS last_week_revenue")}` -- the object as a whole won't json.loads,
    # but the "sql" field itself is still an intact double-quoted string.
    from retail_llm.pipeline import _extract_json
    raw = ('{"intent": "data_query", "sql": "SELECT (SELECT 1 FROM t WHERE '
           'x = \'a\') AS this_week, (SELECT 2 FROM t WHERE x = \'b\') AS '
           'last_week")}')
    obj = _extract_json(raw)
    assert obj["sql"].startswith("SELECT (SELECT 1")
    assert "last_week" in obj["sql"]


def test_compare_ranges_computes_both_periods_independently():
    from retail_llm.pipeline import _compare_ranges
    from retail_llm.config import now
    ranges = _compare_ranges("Total revenue this week vs last week", now())
    assert ranges is not None
    (a1, b1), (a2, b2) = ranges
    assert (b1 - a1).days == 7 and (b2 - a2).days == 7
    assert a2 < a1  # "last week" starts before "this week"
    assert (a1 - a2).days == 7  # exactly the week immediately before, not a month


@needs_llm
def test_week_vs_week_comparison_uses_correct_seven_day_windows():
    # regression: the model computed "last week" as a full month back
    # (2026-07-24..2026-08-24) instead of the 7 days before this week.
    r = answer("Total revenue this week vs last week")
    assert "2026-08-17" in r["sql"] and "2026-08-24" in r["sql"] and "2026-08-31" in r["sql"]
    distinct_periods = {row.get("period") for row in r["result"]}
    assert len(distinct_periods) == 2


@needs_llm
def test_breakdown_by_category_follow_up_keeps_date_scope_and_joins_products():
    # regression: "breakdown by category" right after "sales yesterday" was
    # either erroring (SELECT category FROM transactions -- no such column)
    # or silently losing the "yesterday" scope and returning all-time totals.
    history = []
    r1 = answer("sales yesterday", history)
    history.append({"question": "sales yesterday", "sql": r1["sql"], "answer": r1["answer"]})
    r2 = answer("breakdown by category", history)
    assert "category" in r2["sql"].lower()
    assert "products" in r2["sql"].lower() and "transaction_items" in r2["sql"].lower()
    assert "2026-08-26" in r2["sql"] and "2026-08-27" in r2["sql"]
