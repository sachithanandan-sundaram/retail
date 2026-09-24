"""Regression suite — representative questions checked against the LIVE db.

Two kinds of check per case:
  structural — the generated SQL has the right shape (aggregates, filters)
  value      — the number matches an independent direct query

Run:  RETAIL_NOW=2026-08-27T15:00:00  python -m pytest -q
(these tests only exercise the deterministic fast-path + repair layers, so they
pass with or without the local LLM installed)
"""
import os
import re

os.environ.setdefault("RETAIL_NOW", "2026-08-27T20:00:00")

import pytest

from retail_llm.config import DB_PATH, now
from retail_llm.db import run_readonly
from retail_llm.dates import extract_range
from retail_llm.fast_paths import try_fast_path
from retail_llm.pipeline import plan
from retail_llm.repair import diagnose, needs_aggregation, validate_sql

pytestmark = pytest.mark.skipif(not DB_PATH.exists(),
                                reason="run `python -m retail_llm.generate_data` first")


def _one(sql, params=()):
    rows = run_readonly(sql, params)
    return rows[0] if rows else {}


# --------------------------------------------------------------------------
# fast-path coverage: every confirmed demo question must resolve to a plan
# --------------------------------------------------------------------------
DEMO_QUESTIONS = [
    "What items are below threshold right now?",
    "Show me stock items sitting unsold for more than 30 days",
    "Show me stock items sitting unsold for more than 90 days",
    # "How much stock do I have for <product>" now goes through the LLM + the
    # find_product tool (see test_find_product_tool below), not a fast path.
    "What was total revenue today?",
    "What was total revenue this week?",
    "List the top 10 highest-value bills this month",
    "How much has Priya billed this week?",
    "Which counter person has billed the most this week?",
    "What's the average bill value this month?",
    "What's the average bill value handled by Rahul Verma?",
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


@pytest.mark.parametrize("q", DEMO_QUESTIONS)
def test_demo_question_has_fast_path(q):
    fp = try_fast_path(q)
    assert fp is not None, f"no fast path for: {q}"
    # must actually execute
    rows = run_readonly(fp["sql"], tuple(fp["params"]))
    assert isinstance(rows, list)


# --------------------------------------------------------------------------
# value checks — fast-path result matches an independent direct query
# --------------------------------------------------------------------------
def test_revenue_today_matches_db():
    a, b = extract_range("today", now())
    fp = try_fast_path("What was total revenue today?")
    got = run_readonly(fp["sql"], tuple(fp["params"]))[0]
    exp = _one("SELECT ROUND(SUM(total_amount),2) r, COUNT(*) c FROM transactions "
               "WHERE ts >= ? AND ts < ?", (a.isoformat(), b.isoformat()))
    assert got["revenue"] == exp["r"] and got["bill_count"] == exp["c"]


def test_below_threshold_matches_db():
    fp = try_fast_path("What items are below threshold right now?")
    got = run_readonly(fp["sql"], tuple(fp["params"]))
    exp = _one("SELECT COUNT(*) c FROM products WHERE current_stock < reorder_threshold")
    assert len(got) == exp["c"]


def test_top_cashier_is_sorted_desc():
    fp = try_fast_path("Which counter person has billed the most this week?")
    rows = run_readonly(fp["sql"], tuple(fp["params"]))
    totals = [r["total_billed"] for r in rows]
    assert totals == sorted(totals, reverse=True)
    a, b = extract_range("this week", now())
    exp = _one("SELECT s.name n, ROUND(SUM(t.total_amount),2) v FROM transactions t "
               "JOIN staff s ON s.staff_id=t.cashier_id WHERE t.ts>=? AND t.ts<? "
               "GROUP BY s.staff_id ORDER BY v DESC LIMIT 1", (a.isoformat(), b.isoformat()))
    assert rows[0]["cashier"] == exp["n"]


def test_footfall_today_matches_db():
    a, b = extract_range("today", now())
    fp = try_fast_path("What was today's total footfall?")
    got = run_readonly(fp["sql"], tuple(fp["params"]))[0]
    exp = _one("SELECT SUM(count) s FROM footfall WHERE ts>=? AND ts<?",
               (a.isoformat(), b.isoformat()))
    assert got["total_footfall"] == exp["s"]


def test_customer_lookup_returns_one_row():
    fp = try_fast_path("Show me everything about customer #1234")
    rows = run_readonly(fp["sql"], tuple(fp["params"]))
    assert len(rows) == 1 and rows[0]["customer_id"] == 1234


def test_exceptions_only_flagged_rows():
    fp = try_fast_path("List all billing exceptions from today")
    rows = run_readonly(fp["sql"], tuple(fp["params"]))
    for r in rows:
        assert r["exception_type"] is not None


# --------------------------------------------------------------------------
# repair / validation layer
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


def test_diagnose_catches_missing_category():
    d = diagnose("how many Beverages did we sell",
                 "SELECT COUNT(*) FROM transaction_items")
    assert d and "Beverages" in d


def test_find_product_tool_resolves_typos_and_partials():
    from retail_llm.tools import find_product, repair_product_literals
    # a real catalogue product to test against (brand-word prefix is randomised)
    paneer = _one("SELECT name FROM products WHERE name LIKE '%Paneer%' LIMIT 1").get("name")
    assert paneer
    # partial name
    assert any("Basmati Rice" in m["name"] for m in find_product("basmati rice")["matches"])
    # per-word typo: 'panner' -> 'paneer'
    names = [m["name"] for m in find_product("panner 200g")["matches"]]
    assert paneer in names
    # nonsense -> no match, caller falls back to the model
    assert find_product("xyzzy widget")["matches"] == []
    # value-linking repair rewrites an unresolvable literal
    sql, note = repair_product_literals(
        "SELECT price FROM products WHERE name LIKE '%panner 200g%'")
    assert paneer in sql and note
    # a real exact name is left untouched
    real = _one("SELECT name FROM products LIMIT 1")["name"]
    sql2, note2 = repair_product_literals(
        f"SELECT price FROM products WHERE name = '{real}'")
    assert note2 is None and sql2.strip().endswith(f"'{real}'")


def test_bill_lookup_returns_all_line_items():
    fp = try_fast_path("show me what was in bill #15000")
    assert fp is not None
    rows = run_readonly(fp["sql"], tuple(fp["params"]))
    n_items = _one("SELECT COUNT(*) c FROM transaction_items WHERE transaction_id=15000")["c"]
    assert len(rows) == n_items and n_items > 0
    assert all(r["bill_no"] == 15000 and r["cashier"] for r in rows)


def test_plan_uses_fast_path_source():
    p = plan("What was total revenue today?")
    assert p["source"] == "fast_path" and p["trusted"]


# --------------------------------------------------------------------------
# pipeline: intents, stages, answer shape (deterministic — no LLM needed)
# --------------------------------------------------------------------------
def test_greeting_intent_short_circuits():
    from retail_llm.pipeline import answer
    r = answer("hi there")
    assert r["intent"] == "greeting"
    assert r["sql"] is None
    assert [s["name"] for s in r["stages"]] == ["Understanding the question"]


def test_unsupported_intent():
    from retail_llm.pipeline import answer
    assert answer("asdkjhqwe")["intent"] == "unsupported"


def test_data_query_answer_has_stages_and_matches_db():
    from retail_llm.pipeline import answer
    r = answer("What was total revenue today?")
    assert r["intent"] == "data_query"
    names = [s["name"] for s in r["stages"]]
    assert "Query building" in names and "Database execution" in names and "Answer generation" in names
    exp = _one("SELECT ROUND(SUM(total_amount),2) r FROM transactions "
               "WHERE ts >= '2026-08-27T00:00:00' AND ts < '2026-08-28T00:00:00'")["r"]
    # deterministic phrasing (no model in CI) states the figure outright;
    # with a model loaded it may render it as lakh — just require a real answer.
    from retail_llm import llm
    if not llm.available():
        assert str(round(exp)) in r["answer"].replace(",", "")
    assert len(r["answer"].split()) >= 3


def test_answer_stream_emits_meta_and_done():
    from retail_llm.pipeline import answer_stream
    events = [ev for ev, _ in answer_stream("What was total revenue today?")]
    assert events[0] == "meta" and events[-1] == "done"
