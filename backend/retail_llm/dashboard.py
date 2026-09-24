"""Deterministic dashboard aggregates — no LLM anywhere in this path.

Mirrors the shape of the FQC project's /dashboard/fqc endpoint, retargeted to
retail KPIs.
"""
from datetime import timedelta

from .config import now
from .db import run_readonly


def _q1(sql, params=()):
    rows = run_readonly(sql, params)
    return rows[0] if rows else {}


def bill(bill_no: int):
    """Full detail for one bill: header (who/when/how long) + every line item."""
    header = _q1(
        "SELECT t.transaction_id AS bill_no, t.ts AS billed_at, t.start_time, t.end_time, "
        "t.bill_seconds, s.name AS cashier, s.staff_id AS cashier_id, "
        "t.customer_id, c.name AS customer, c.phone AS customer_phone, "
        "t.subtotal, t.discount_pct, t.total_amount, t.is_exception, t.exception_type "
        "FROM transactions t JOIN staff s ON s.staff_id = t.cashier_id "
        "LEFT JOIN customers c ON c.customer_id = t.customer_id "
        "WHERE t.transaction_id = ?", (bill_no,))
    if not header:
        return None
    header["items"] = run_readonly(
        "SELECT ti.product_id, p.name, p.category, ti.quantity, ti.unit_price, ti.line_total "
        "FROM transaction_items ti JOIN products p ON p.product_id = ti.product_id "
        "WHERE ti.transaction_id = ? ORDER BY ti.line_total DESC", (bill_no,))
    return header


def summary(days: int = 14) -> dict:
    days = max(2, min(int(days), 180))
    n = now()
    today0 = n.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow0 = today0 + timedelta(days=1)
    week0 = today0 - timedelta(days=today0.weekday())
    last_week0 = week0 - timedelta(days=7)
    month0 = today0.replace(day=1)
    d14 = today0 - timedelta(days=days - 1)

    rev_today = _q1("SELECT ROUND(COALESCE(SUM(total_amount),0),2) v, COUNT(*) c "
                    "FROM transactions WHERE ts>=? AND ts<?", (today0.isoformat(), tomorrow0.isoformat()))
    rev_week = _q1("SELECT ROUND(COALESCE(SUM(total_amount),0),2) v FROM transactions "
                   "WHERE ts>=? AND ts<?", (week0.isoformat(), tomorrow0.isoformat()))
    rev_last_week = _q1("SELECT ROUND(COALESCE(SUM(total_amount),0),2) v FROM transactions "
                        "WHERE ts>=? AND ts<?", (last_week0.isoformat(), week0.isoformat()))
    avg_bill_month = _q1("SELECT ROUND(COALESCE(AVG(total_amount),0),2) v FROM transactions "
                         "WHERE ts>=?", (month0.isoformat(),))
    footfall_today = _q1("SELECT COALESCE(SUM(count),0) v FROM footfall WHERE ts>=? AND ts<?",
                         (today0.isoformat(), tomorrow0.isoformat()))
    below = _q1("SELECT COUNT(*) v FROM products WHERE current_stock < reorder_threshold")
    exc_today = _q1("SELECT COUNT(*) v FROM transactions WHERE is_exception=1 AND ts>=? AND ts<?",
                    (today0.isoformat(), tomorrow0.isoformat()))
    dead = _q1("SELECT COUNT(*) v FROM ("
               "  SELECT p.product_id FROM products p "
               "  LEFT JOIN transaction_items ti ON ti.product_id=p.product_id "
               "  LEFT JOIN transactions t ON t.transaction_id=ti.transaction_id "
               "  GROUP BY p.product_id "
               "  HAVING MAX(t.ts) IS NULL OR MAX(t.ts) < ?)",
               ((today0 - timedelta(days=30)).isoformat(),))

    revenue_by_day = run_readonly(
        "SELECT substr(ts,1,10) day, ROUND(SUM(total_amount),2) revenue, COUNT(*) bills "
        "FROM transactions WHERE ts>=? GROUP BY day ORDER BY day", (d14.isoformat(),))

    sales_by_hour = run_readonly(
        "SELECT strftime('%H', ts) hour, ROUND(SUM(total_amount),2) revenue, COUNT(*) bills "
        "FROM transactions WHERE ts>=? GROUP BY hour ORDER BY hour", (month0.isoformat(),))

    footfall_by_hour = run_readonly(
        "SELECT strftime('%H', ts) hour, SUM(count) footfall FROM footfall WHERE ts>=? "
        "GROUP BY hour ORDER BY hour", (month0.isoformat(),))

    top_products = run_readonly(
        "SELECT p.name, p.category, SUM(ti.quantity) units, ROUND(SUM(ti.line_total),2) revenue "
        "FROM transaction_items ti JOIN transactions t ON t.transaction_id=ti.transaction_id "
        "JOIN products p ON p.product_id=ti.product_id WHERE t.ts>=? "
        "GROUP BY p.product_id ORDER BY units DESC LIMIT 10", (week0.isoformat(),))

    cashiers = run_readonly(
        "SELECT s.name cashier, ROUND(SUM(t.total_amount),2) billed, COUNT(*) bills, "
        "ROUND(AVG(t.bill_seconds),1) avg_seconds "
        "FROM transactions t JOIN staff s ON s.staff_id=t.cashier_id WHERE t.ts>=? "
        "GROUP BY s.staff_id ORDER BY billed DESC", (week0.isoformat(),))

    low_stock = run_readonly(
        "SELECT name, category, current_stock, reorder_threshold FROM products "
        "WHERE current_stock < reorder_threshold "
        "ORDER BY (reorder_threshold-current_stock) DESC LIMIT 15")

    recent_exceptions = run_readonly(
        "SELECT t.ts, s.name cashier, t.exception_type, t.discount_pct, t.total_amount "
        "FROM transactions t JOIN staff s ON s.staff_id=t.cashier_id "
        "WHERE t.is_exception=1 ORDER BY t.ts DESC LIMIT 15")

    top_categories = run_readonly(
        "SELECT p.category, ROUND(SUM(ti.line_total),2) revenue, SUM(ti.quantity) units "
        "FROM transaction_items ti JOIN transactions t ON t.transaction_id=ti.transaction_id "
        "JOIN products p ON p.product_id=ti.product_id WHERE t.ts>=? "
        "GROUP BY p.category ORDER BY revenue DESC LIMIT 8", (month0.isoformat(),))

    recent_bills = run_readonly(
        "SELECT t.transaction_id AS bill_no, t.ts, s.name cashier, c.name customer, "
        "t.bill_seconds, t.total_amount, t.is_exception "
        "FROM transactions t JOIN staff s ON s.staff_id=t.cashier_id "
        "LEFT JOIN customers c ON c.customer_id=t.customer_id "
        "ORDER BY t.ts DESC LIMIT 20")

    return {
        "now": n.isoformat(),
        "store": "Hypermart — Main Branch",
        "window_days": days,
        "kpis": {
            "revenue_today": rev_today.get("v", 0),
            "bills_today": rev_today.get("c", 0),
            "revenue_week": rev_week.get("v", 0),
            "revenue_last_week": rev_last_week.get("v", 0),
            "avg_bill_month": avg_bill_month.get("v", 0),
            "footfall_today": footfall_today.get("v", 0),
            "below_threshold": below.get("v", 0),
            "exceptions_today": exc_today.get("v", 0),
            "dead_stock": dead.get("v", 0),
        },
        "revenue_by_day": revenue_by_day,
        "sales_by_hour": sales_by_hour,
        "footfall_by_hour": footfall_by_hour,
        "top_products": top_products,
        "top_categories": top_categories,
        "cashiers": cashiers,
        "low_stock": low_stock,
        "recent_exceptions": recent_exceptions,
        "recent_bills": recent_bills,
    }
