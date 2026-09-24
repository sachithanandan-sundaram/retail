"""Deterministic, multi-section sales reports for any period granularity —
day, week, month, quarter or year.

A "report" is a different shape of request than a single Q&A: it runs several
targeted SQL queries (revenue, top categories/products/cashiers, peak day/hour,
exceptions, footfall) and composes them into one professional summary plus
supporting breakdown tables. Everything here is plain SQL + Python arithmetic
and string formatting — no model call, so a report can be produced even
without the LLM loaded.
"""
import re
from datetime import datetime, timedelta

from .dates import _add_month, _day_start, _month_start, _week_start, extract_range
from .db import run_readonly
from .phrase import money

_REPORT_RE = re.compile(r"\breport\b|\bsummary\b", re.I)
_KIND_PATTERNS = [
    ("day", re.compile(r"\bdaily\b|\bper day\b|\bday\b", re.I)),
    ("week", re.compile(r"\bweekly\b|\bper week\b|\bweek\b", re.I)),
    ("quarter", re.compile(r"\bquarterly\b|\bper quarter\b|\bquarter\b", re.I)),
    ("year", re.compile(r"\byearly\b|\bannual(ly)?\b|\bper year\b|\byear\b", re.I)),
    ("month", re.compile(r"\bmonthly\b|\bper month\b|\bmonth\b", re.I)),
]


def is_report_request(question: str) -> bool:
    return bool(_REPORT_RE.search(question or ""))


def _kind(question: str) -> str:
    for name, rx in _KIND_PATTERNS:
        if rx.search(question):
            return name
    return "month"


def _default_range(kind, now):
    if kind == "day":
        s = _day_start(now)
        return (s, s + timedelta(days=1))
    if kind == "week":
        s = _week_start(now)
        return (s, s + timedelta(days=7))
    if kind == "quarter":
        q = (now.month - 1) // 3
        s = datetime(now.year, q * 3 + 1, 1)
        return (s, _add_month(s, 3))
    if kind == "year":
        return (datetime(now.year, 1, 1), datetime(now.year + 1, 1, 1))
    s = _month_start(now)
    return (s, _add_month(s, 1))


def _previous_range(rng, kind):
    s, e = rng
    if kind == "month":
        return (_add_month(s, -1), s)
    if kind == "quarter":
        return (_add_month(s, -3), s)
    if kind == "year":
        return (datetime(s.year - 1, 1, 1), datetime(s.year, 1, 1))
    dur = e - s
    return (s - dur, s)


def _period_label(kind, a, b):
    if kind == "year":
        return str(a.year)
    if kind == "month":
        return a.strftime("%B %Y")
    if kind == "quarter":
        q = (a.month - 1) // 3 + 1
        return f"Q{q} {a.year}"
    if kind == "week":
        return f"{a.strftime('%d %b %Y')} – {(b - timedelta(days=1)).strftime('%d %b %Y')}"
    return a.strftime("%d %b %Y")


def _one(sql, params=()):
    rows = run_readonly(sql, params)
    return rows[0] if rows else {}


def build(question: str, now):
    """-> dict(answer, sql, result, source), or None if not a report request."""
    if not is_report_request(question):
        return None

    kind = _kind(question)
    rng = extract_range(question, now) or _default_range(kind, now)
    a, b = rng
    prev_a, prev_b = _previous_range(rng, kind)
    a_iso, b_iso = a.isoformat(), b.isoformat()
    pa_iso, pb_iso = prev_a.isoformat(), prev_b.isoformat()

    cur = _one("SELECT ROUND(SUM(total_amount),2) AS revenue, COUNT(*) AS bills, "
               "ROUND(AVG(total_amount),2) AS avg_bill FROM transactions "
               "WHERE ts >= ? AND ts < ?", (a_iso, b_iso))
    prev = _one("SELECT ROUND(SUM(total_amount),2) AS revenue FROM transactions "
                "WHERE ts >= ? AND ts < ?", (pa_iso, pb_iso))
    exceptions = _one("SELECT COUNT(*) AS n FROM transactions WHERE is_exception = 1 "
                      "AND ts >= ? AND ts < ?", (a_iso, b_iso))
    footfall = _one("SELECT SUM(count) AS n FROM footfall WHERE ts >= ? AND ts < ?", (a_iso, b_iso))

    top_categories = run_readonly(
        "SELECT p.category AS category, ROUND(SUM(ti.line_total),2) AS revenue, "
        "SUM(ti.quantity) AS units FROM transaction_items ti "
        "JOIN transactions t ON t.transaction_id = ti.transaction_id "
        "JOIN products p ON p.product_id = ti.product_id "
        "WHERE t.ts >= ? AND t.ts < ? GROUP BY p.category ORDER BY revenue DESC LIMIT 5",
        (a_iso, b_iso))

    top_products = run_readonly(
        "SELECT p.name AS product, ROUND(SUM(ti.line_total),2) AS revenue, "
        "SUM(ti.quantity) AS units FROM transaction_items ti "
        "JOIN transactions t ON t.transaction_id = ti.transaction_id "
        "JOIN products p ON p.product_id = ti.product_id "
        "WHERE t.ts >= ? AND t.ts < ? GROUP BY p.product_id ORDER BY revenue DESC LIMIT 5",
        (a_iso, b_iso))

    top_cashiers = run_readonly(
        "SELECT s.name AS cashier, ROUND(SUM(t.total_amount),2) AS revenue, "
        "COUNT(*) AS bills FROM transactions t JOIN staff s ON s.staff_id = t.cashier_id "
        "WHERE t.ts >= ? AND t.ts < ? GROUP BY s.staff_id ORDER BY revenue DESC LIMIT 5",
        (a_iso, b_iso))

    revenue_by_weekday = run_readonly(
        "SELECT CASE strftime('%w', ts) WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' "
        "WHEN '2' THEN 'Tuesday' WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday' "
        "WHEN '5' THEN 'Friday' ELSE 'Saturday' END AS weekday, "
        "ROUND(SUM(total_amount),2) AS revenue, COUNT(*) AS bills FROM transactions "
        "WHERE ts >= ? AND ts < ? GROUP BY strftime('%w', ts) ORDER BY revenue DESC LIMIT 7",
        (a_iso, b_iso))

    revenue_by_hour = run_readonly(
        "SELECT strftime('%H', ts) AS hour, ROUND(SUM(total_amount),2) AS revenue, "
        "COUNT(*) AS bills FROM transactions WHERE ts >= ? AND ts < ? "
        "GROUP BY hour ORDER BY revenue DESC LIMIT 24",
        (a_iso, b_iso))

    revenue = cur.get("revenue") or 0
    bills = cur.get("bills") or 0
    avg_bill = cur.get("avg_bill") or 0
    prev_revenue = prev.get("revenue") or 0

    growth = ""
    if prev_revenue:
        pct = (revenue - prev_revenue) / prev_revenue * 100
        direction = "up" if pct >= 0 else "down"
        growth = (f" That is {direction} {abs(pct):.0f}% from the previous {kind}'s "
                  f"{money(prev_revenue)}.")

    label = _period_label(kind, a, b)
    lines = [
        f"Sales Report — {label}",
        "",
        f"Total revenue was {money(revenue)} across {bills:,} bills, an average "
        f"bill value of {money(avg_bill)}.{growth}",
    ]
    if top_categories:
        lines.append(f"Top category: {top_categories[0]['category']} "
                      f"({money(top_categories[0]['revenue'])}).")
    if top_products:
        units = top_products[0]["units"]
        lines.append(f"Best-selling product: {top_products[0]['product']} "
                      f"({money(top_products[0]['revenue'])}, {units} unit{'s' if units != 1 else ''}).")
    if top_cashiers:
        lines.append(f"Top cashier: {top_cashiers[0]['cashier']} "
                      f"({money(top_cashiers[0]['revenue'])} billed).")
    if revenue_by_weekday:
        lines.append(f"Busiest day: {revenue_by_weekday[0]['weekday']} "
                      f"({money(revenue_by_weekday[0]['revenue'])}).")
    if revenue_by_hour:
        lines.append(f"Busiest hour: {revenue_by_hour[0]['hour']}:00 "
                      f"({money(revenue_by_hour[0]['revenue'])}).")
    if exceptions.get("n"):
        lines.append(f"{exceptions['n']} billing exception(s) were flagged in the period.")
    if footfall.get("n"):
        lines.append(f"Total footfall for the period was {int(footfall['n']):,}.")

    facets = {
        "top_categories": top_categories,
        "top_products": top_products,
        "top_cashiers": top_cashiers,
        "revenue_by_weekday": revenue_by_weekday,
        "revenue_by_hour": revenue_by_hour,
    }
    return {
        "answer": "\n".join(lines),
        "sql": (f"-- report for {label}: revenue/bills/avg-bill vs previous {kind}, "
                f"top categories, top products, top cashiers, revenue by weekday, "
                f"revenue by hour, exceptions, footfall"),
        "result": [facets],
        "source": "report",
    }
