"""The reliability layer: turn a model's SQL string into something safe to run,
and flag pipelines that don't actually answer the question.

Everything here is deterministic. Prompt instructions are advisory; code that
inspects and rewrites the output is not.
"""
import re

from .config import MAX_ROWS

_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|analyze)\b", re.I)
_AGG_RE = re.compile(r"\b(count|sum|avg|min|max|group\s+by)\b", re.I)

CATEGORIES = [
    "Fruits & Vegetables", "Groceries & Staples", "Dairy & Eggs", "Bakery", "Beverages",
    "Snacks & Packaged Foods", "Frozen Foods", "Household Care", "Personal Care",
    "Health & Wellness", "Baby Care", "Pet Care", "Beauty & Cosmetics", "Kitchen & Dining",
    "Home & Furnishing", "Toys & Games", "Books & Stationery", "Sports & Fitness",
    "Automotive & Travel", "Men's Clothing", "Women's Clothing", "Kids' Clothing",
    "Footwear", "Bags & Luggage", "Watches & Accessories", "Mobiles & Tablets",
    "Laptops & Computing", "Televisions", "Large Appliances", "Small Appliances",
    "Electronics Accessories",
]
# keyword -> exact category, for the "question names a category but SQL ignores it" check
CATEGORY_KEYWORDS = {
    "fruits": "Fruits & Vegetables", "vegetables": "Fruits & Vegetables", "produce": "Fruits & Vegetables",
    "groceries": "Groceries & Staples", "staples": "Groceries & Staples",
    "dairy": "Dairy & Eggs", "eggs": "Dairy & Eggs",
    "bakery": "Bakery", "beverages": "Beverages", "drinks": "Beverages",
    "snacks": "Snacks & Packaged Foods", "frozen": "Frozen Foods",
    "household": "Household Care", "personal care": "Personal Care",
    "wellness": "Health & Wellness", "pharmacy": "Health & Wellness",
    "baby care": "Baby Care", "pet care": "Pet Care", "pet": "Pet Care",
    "beauty": "Beauty & Cosmetics", "cosmetics": "Beauty & Cosmetics", "makeup": "Beauty & Cosmetics",
    "kitchen": "Kitchen & Dining", "cookware": "Kitchen & Dining",
    "furniture": "Home & Furnishing", "furnishing": "Home & Furnishing",
    "toys": "Toys & Games", "stationery": "Books & Stationery", "books": "Books & Stationery",
    "sports": "Sports & Fitness", "fitness": "Sports & Fitness",
    "automotive": "Automotive & Travel", "luggage": "Bags & Luggage", "bags": "Bags & Luggage",
    "footwear": "Footwear", "shoes": "Footwear",
    "watches": "Watches & Accessories",
    "mobiles": "Mobiles & Tablets", "smartphones": "Mobiles & Tablets", "phones": "Mobiles & Tablets",
    "tablets": "Mobiles & Tablets",
    "laptops": "Laptops & Computing", "computing": "Laptops & Computing",
    "televisions": "Televisions", "tv": "Televisions", "tvs": "Televisions",
    "large appliances": "Large Appliances", "small appliances": "Small Appliances",
    "appliances": "Small Appliances",
    "men's clothing": "Men's Clothing", "mens clothing": "Men's Clothing",
    "women's clothing": "Women's Clothing", "womens clothing": "Women's Clothing",
    "kids clothing": "Kids' Clothing", "apparel": "Men's Clothing", "clothing": "Men's Clothing",
    "electronics accessories": "Electronics Accessories", "accessories": "Electronics Accessories",
}
EXCEPTION_TYPES = ["VOID_WITHOUT_SCAN", "HIGH_DISCOUNT", "MANUAL_PRICE_OVERRIDE", "NO_SALE_OPEN"]
ROLES = ["CASHIER", "FLOOR", "MANAGER"]

# canonical enum values keyed by a normalised form (lowercase, & -> and,
# _/space collapsed) so a literal the small model wrote loosely still matches.
_ENUM_BY_NORM = {}
for _v in CATEGORIES + EXCEPTION_TYPES + ROLES:
    _k = _v.lower().replace("&", "and").replace("_", " ")
    _k = " ".join(_k.split())
    _ENUM_BY_NORM[_k] = _v


def canonicalize_enums(sql: str) -> str:
    """Rewrite any quoted literal that matches a known category / exception_type
    / role up to case, '&' vs 'and', and space vs underscore. Small models
    mirror the user's phrasing ("Mobiles and Tablets", "high discount"), so
    this deterministic guardrail matters — same idea as the SDK sql-demo's
    canonicalize_enums(), extended for the retail enums."""
    def fix(m):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        norm = " ".join(raw.lower().replace("&", "and").replace("_", " ").split())
        real = _ENUM_BY_NORM.get(norm)
        return f"'{real}'" if real and real != raw else m.group(0)
    return re.sub(r"'([^']+)'|\"([^\"]+)\"", fix, sql)

_AGG_QUESTION_RE = re.compile(
    r"\b(how many|how much|total|average|avg|count|number of|per |each |"
    r"top \d+|most|least|highest|lowest|fastest|slowest|peak|busiest)\b", re.I)


class ValidationError(Exception):
    pass


def validate_sql(sql: str) -> str:
    """Raise ValidationError on anything not a single read-only SELECT.
    Returns the cleaned SQL (trailing ; stripped, LIMIT enforced)."""
    s = sql.strip().rstrip(";").strip()
    if not s:
        raise ValidationError("empty SQL")
    # single statement only
    if ";" in s:
        raise ValidationError("multiple statements")
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise ValidationError("not a SELECT")
    if _WRITE_KEYWORDS.search(s):
        raise ValidationError("write/DDL keyword present")
    # enforce a row cap unless it's clearly a single-aggregate query
    if not re.search(r"\blimit\s+\d+", low):
        if re.search(r"\bgroup\s+by\b", low) or not _AGG_RE.search(low):
            s = f"{s}\nLIMIT {MAX_ROWS}"
    else:
        s = re.sub(r"\blimit\s+(\d+)\b",
                   lambda m: f"LIMIT {min(int(m.group(1)), MAX_ROWS)}", s, flags=re.I)
    return s


def needs_aggregation(question: str, sql: str) -> bool:
    """True if the question clearly wants a computed number but the SQL just
    filters/lists rows."""
    if not _AGG_QUESTION_RE.search(question):
        return False
    # "show me everything about customer X" / "list bills" are genuinely row dumps
    if re.search(r"\b(show me everything|list all|show all|history)\b", question, re.I):
        return False
    return not _AGG_RE.search(sql)


def missing_category_filter(question: str, sql: str):
    """If the question names a product category but the SQL never filters to it."""
    q, s = question.lower(), sql.lower()
    for cat in CATEGORIES:
        if cat.lower() in q and cat.lower() not in s:
            return cat
    for kw, cat in CATEGORY_KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", q) and cat.lower() not in s:
            return cat
    return None


def missing_exception_filter(question: str, sql: str) -> bool:
    q = question.lower()
    if re.search(r"\b(exception|void[- ]?without[- ]?scan|billing anomal)", q):
        return "is_exception" not in sql.lower() and "exception_type" not in sql.lower()
    return False


def wrong_items_date_column(sql: str):
    """transaction_items has no ts/date column — a small model's most common
    join mistake is filtering `<items_alias>.ts` directly. Catch it before
    execution (it would otherwise just error) and name the exact fix."""
    m = re.search(r"\btransaction_items\s+(?:as\s+)?(\w+)\b", sql, re.I)
    if not m:
        return None
    alias = m.group(1)
    if alias.lower() in ("as", "on", "where", "group", "order", "join"):
        return None
    if re.search(rf"\b{re.escape(alias)}\.ts\b", sql, re.I):
        return (f"`{alias}` is transaction_items, which has no ts column — join "
                f"transactions and filter on ITS alias's ts instead.")
    return None


def wrong_footfall_aggregation(question: str, sql: str) -> bool:
    """'total footfall' needs SUM(count) — COUNT(*) counts hourly rows, not
    people, and always undercounts."""
    q = question.lower()
    if not re.search(r"\bfootfall\b|\bfoot\s*traffic\b", q):
        return False
    if "footfall" not in sql.lower():
        return False
    return bool(re.search(r"count\s*\(\s*\*\s*\)", sql, re.I)) and \
        not re.search(r"sum\s*\(\s*\w*\.?count\s*\)", sql, re.I)


def wrong_customer_profile_table(question: str, sql: str) -> bool:
    """'everything about / profile of / who is customer X' wants the customers
    row, not their transactions."""
    q = question.lower()
    if not re.search(r"\b(everything about|profile of|who is)\b.*\bcustomer\b", q):
        return False
    low = sql.lower()
    return "from customers" not in low and "join customers" not in low


def unrequested_date_filter(question: str, sql: str, has_range: bool):
    """A question with no date wording and no interpreted range shouldn't get
    a ts/date_added/last_sold_date filter — a small model sometimes adds
    'today' out of habit (e.g. treating 'right now' as a date filter instead
    of 'current column value')."""
    if has_range:
        return None
    q = question.lower()
    if re.search(r"\b(today|yesterday|this week|last week|this month|last month|"
                 r"this year|last year|this quarter|last quarter|since|between|"
                 r"from .+ to |on \d|in the last|past \d)\b", q):
        return None
    if re.search(r"\b(date_added|last_sold_date)\s*(>=|<=|>|<|=)", sql, re.I) or \
       re.search(r"\bts\s*(>=|<=|>|<|=)\s*'\d{4}-\d{2}-\d{2}", sql, re.I):
        return ("the question doesn't ask for a specific date/period, but the SQL "
                "filters by one anyway — drop that date filter (e.g. 'right now' "
                "means the current value of a column, not a date range).")
    return None


def wrong_time_grouping(question: str, sql: str):
    """'time of day' wants an hour grouping; 'day of week' wants weekday."""
    q = question.lower()
    low = sql.lower()
    if "day of week" in q or "day of the week" in q:
        if "%w" not in low and "strftime('%w'" not in low.replace(" ", ""):
            return "group by day-of-week: strftime('%w', ts)"
    if "time of day" in q or "which hour" in q or "busiest hour" in q or "peak hour" in q:
        if "%h" not in low:
            return "group by hour-of-day: strftime('%H', ts)"
    return None


def diagnose(question: str, sql: str):
    """Return a human-readable problem string to feed back to the model, or None."""
    items_date = wrong_items_date_column(sql)
    if items_date:
        return items_date
    if wrong_footfall_aggregation(question, sql):
        return "footfall needs SUM(count), not COUNT(*) — COUNT(*) counts hours, not people."
    if wrong_customer_profile_table(question, sql):
        return ("The question wants the customer's profile — SELECT from the customers "
                "table (name, phone, visit_count, total_spend, ...), not transactions.")
    if needs_aggregation(question, sql):
        return ("This question asks for a computed figure (count/total/average/"
                "ranking) but the SQL only filters rows. Use COUNT/SUM/AVG and "
                "GROUP BY as needed.")
    cat = missing_category_filter(question, sql)
    if cat:
        return f"The question is about the '{cat}' category but the SQL never filters category = '{cat}'."
    if missing_exception_filter(question, sql):
        return "The question is about billing exceptions but the SQL never filters is_exception = 1."
    tg = wrong_time_grouping(question, sql)
    if tg:
        return f"The question asks about time patterns — {tg}."
    from .dates import extract_range
    from .config import now
    has_range = bool(extract_range(question, now()))
    udf = unrequested_date_filter(question, sql, has_range)
    if udf:
        return udf
    return None
