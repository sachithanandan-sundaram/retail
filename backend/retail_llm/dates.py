"""Deterministic relative-date parsing.

Overrides the model's own (unreliable) date arithmetic: we compute the range
in code and both (a) feed it into the query prompt and (b) re-check it after.

Returns a half-open [start, end) tuple of datetimes, or None if unrecognized.
"""
import re
from datetime import datetime, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


def _day_start(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _week_start(d: datetime) -> datetime:
    return _day_start(d) - timedelta(days=d.weekday())  # Monday


def _month_start(d: datetime) -> datetime:
    return _day_start(d).replace(day=1)


def _add_month(d: datetime, n: int) -> datetime:
    m = d.month - 1 + n
    return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=1)


def extract_range(text: str, now: datetime):
    t = text.lower().strip()

    # --- explicit "today / yesterday" -----------------------------------
    if re.search(r"\btoday\b|\bso far today\b|\bright now\b", t):
        s = _day_start(now)
        return (s, s + timedelta(days=1))
    if re.search(r"\byesterday\b", t):
        s = _day_start(now) - timedelta(days=1)
        return (s, s + timedelta(days=1))

    # --- this / last week|month|year|quarter ---------------------------
    m = re.search(r"\b(this|last|previous|past)\s+(week|month|year|quarter)\b", t)
    if m:
        which, unit = m.group(1), m.group(2)
        back = which in ("last", "previous")
        if unit == "week":
            s = _week_start(now) - (timedelta(days=7) if back else timedelta())
            return (s, s + timedelta(days=7))
        if unit == "month":
            s = _add_month(_month_start(now), -1 if back else 0)
            return (s, _add_month(s, 1))
        if unit == "year":
            y = now.year - (1 if back else 0)
            return (datetime(y, 1, 1), datetime(y + 1, 1, 1))
        if unit == "quarter":
            q = (now.month - 1) // 3
            s = datetime(now.year, q * 3 + 1, 1)
            if back:
                s = _add_month(s, -3)
            return (s, _add_month(s, 3))

    # --- last N minutes/hours/days/weeks/months ------------------------
    m = re.search(r"\b(?:last|past|previous)\s+(\d+)\s*(minute|hour|day|week|month)s?\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "minute":
            return (now - timedelta(minutes=n), now)
        if unit == "hour":
            return (now - timedelta(hours=n), now)
        if unit == "day":
            return (_day_start(now) - timedelta(days=n - 1), _day_start(now) + timedelta(days=1))
        if unit == "week":
            return (_day_start(now) - timedelta(weeks=n), _day_start(now) + timedelta(days=1))
        if unit == "month":
            return (_add_month(_month_start(now), -n), _add_month(_month_start(now), 1))

    # --- "30/60/90 days" (stock aging phrasing) -----------------------
    m = re.search(r"\bmore than\s+(\d+)\s+days?\b", t)
    if m:
        n = int(m.group(1))
        return (datetime.min, now - timedelta(days=n))

    # --- month + year  ("july 2026") / bare month --------------------
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\.?\s*(\d{4})?\b", t)
    if m and m.group(1) in MONTHS:
        mon = MONTHS[m.group(1)]
        yr = int(m.group(2)) if m.group(2) else now.year
        s = datetime(yr, mon, 1)
        return (s, _add_month(s, 1))

    # --- bare year --------------------------------------------------
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        y = int(m.group(1))
        return (datetime(y, 1, 1), datetime(y + 1, 1, 1))

    # --- this week/month with no qualifier already handled; "this month"
    return None


def label(rng) -> str:
    a, b = rng
    return f"{a.isoformat()} .. {b.isoformat()}"
