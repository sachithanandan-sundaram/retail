"""Tools the query-writing model can call before it commits to SQL.

Right now there is one: `find_product`. The model stays in charge of the SQL —
this just lets it look up the *exact* catalogue name/id for a product the user
named loosely or misspelled, instead of guessing a literal that matches nothing.
"""
import difflib
import re

from .db import run_readonly

_STOPWORDS = {
    "the", "a", "an", "of", "for", "is", "are", "was", "were", "be", "me", "please",
    "value", "pack", "some", "any", "my", "our", "we", "you", "i", "it", "us",
    "price", "prices", "cost", "costs", "costing", "stock", "inventory", "mrp", "rate",
    "how", "much", "many", "what", "whats", "which", "does", "do", "did", "have",
    "current", "level", "on", "hand", "in", "left", "available", "now", "today",
    "show", "tell", "give", "get", "and", "or", "to", "per", "unit", "units",
    "there", "each", "sell", "sells", "selling", "sold", "buy", "bought", "carry", "carries",
    "list", "name", "names", "type", "types", "kind", "kinds", "variant", "variants",
    "variety", "varieties", "different", "distinct", "unique", "all", "range",
    "product", "products", "item", "items", "sku", "skus", "brand", "brands", "option", "options",
}


def _tokens(term: str):
    return [t for t in re.findall(r"[a-z0-9]+", term.lower())
            if len(t) > 1 and t not in _STOPWORDS]


def resolve_product_names(term: str, limit: int = 12):
    """Free-text term -> list of real catalogue names, best first. Word-order
    independent, tolerates a missing brand/'word' prefix and per-word typos
    ('pro panner 200g' -> 'Pro Paneer 200g'). [] when nothing is close."""
    raw = _tokens(term)
    if not raw:
        return []
    names = [r["name"] for r in run_readonly("SELECT name FROM products")]
    low = {n: n.lower() for n in names}
    vocab = {w for n in names for w in re.findall(r"[a-z0-9]+", low[n])}

    toks = []
    for t in raw:
        if t in vocab or any(t in w for w in vocab):
            toks.append(t)
        else:
            near = difflib.get_close_matches(t, list(vocab), n=1, cutoff=0.8)
            toks.append(near[0] if near else t)

    def has_all(ts, n):
        return all(t in low[n] for t in ts)

    hits = [n for n in names if has_all(toks, n)]
    if not hits and len(toks) >= 2:          # drop the leading brand/adjective word
        hits = [n for n in names if has_all(toks[1:], n)]
    if not hits and len(toks) >= 2:          # or the trailing size/qualifier word
        hits = [n for n in names if has_all(toks[:-1], n)]
    if not hits and toks:                    # any single significant token
        for t in toks:
            hits = [n for n in names if t in low[n]]
            if hits:
                break
    if not hits:  # last resort: whole-string fuzzy
        close = difflib.get_close_matches(" ".join(toks), list(low.values()), n=limit, cutoff=0.7)
        hits = [n for n in names if low[n] in close]
    hits.sort(key=lambda n: (0 if has_all(toks, n) else 1, len(n)))
    return hits[:limit]


def find_product(query: str, limit: int = 12):
    """Tool body. Returns catalogue rows for the products matching `query`."""
    names = resolve_product_names(query, limit)
    if not names:
        return {"query": query, "matches": []}
    ph = ",".join("?" * len(names))
    rows = run_readonly(
        f"SELECT product_id, name, category, price, current_stock, reorder_threshold "
        f"FROM products WHERE name IN ({ph}) ORDER BY length(name)", names)
    return {"query": query, "matches": rows}


TOOLS = {"find_product": find_product}


def link_products_in_question(question: str, limit: int = 6):
    """Schema/value linking done up front: if the question clearly names a
    specific product, resolve it to real catalogue rows so the prompt can hand
    the model an exact product_id instead of it guessing a name literal.
    Returns [] unless the match is confident (every significant question token
    appears in the product name)."""
    q = question.lower()
    # only when the question is plausibly about a specific product/its price/stock
    if not re.search(r"\b(price|cost|mrp|rate|stock|inventory|how much|units? of|"
                     r"in stock|reorder|do (?:i|we) (?:have|carry|stock))\b", q) \
       and not re.search(r"\b(of|for)\s+[a-z]", q):
        return []
    res = find_product(question, limit)
    matches = res["matches"]
    # guard against a coincidental all-token hit on an unrelated question
    if not matches or len(matches) > limit:
        return []
    return matches


_NAME_LITERAL_RE = re.compile(
    r"(?:lower\s*\(\s*)?((?:\w+\.)?name)\s*\)?\s*(=|==|LIKE|like)\s*'([^']*)'")


def repair_product_literals(sql: str):
    """Deterministic value-linking safety net: if the model wrote a product
    name/LIKE literal that resolves to nothing in the catalogue (a typo or a
    made-up name), swap it for `name IN (<real matches>)` via find_product.
    Returns (sql, note-or-None). The model stays in charge of the query shape;
    this only corrects the product it named."""
    try:
        db_names = [r["name"] for r in run_readonly("SELECT name FROM products")]
    except Exception:
        return sql, None
    by_low = {n.lower(): n for n in db_names}
    notes = []

    def _sub(m):
        col, op, val = m.group(1), m.group(2).upper().replace("==", "="), m.group(3)
        needle = val.strip("%").strip().lower()
        if not needle:
            return m.group(0)
        if op == "LIKE" and any(needle in n for n in by_low):
            return m.group(0)                        # LIKE substring is fine
        if op == "=":
            if val in by_low.values():
                return m.group(0)                    # exact, correct case
            if needle in by_low:                     # right product, wrong case
                real = by_low[needle]
                notes.append(f'"{val}" -> "{real}" (case)')
                return f"{col} = '{real}'"
        matches = resolve_product_names(val.strip("%"))
        if not matches:
            return m.group(0)
        notes.append(f'"{val}" -> {matches[:3]}')
        quoted = ", ".join("'" + x.replace("'", "''") + "'" for x in matches)
        return f"{col} IN ({quoted})"

    fixed = _NAME_LITERAL_RE.sub(_sub, sql)
    return _ensure_product_name(fixed), ("; ".join(notes) if notes else None)


_PRODUCT_ID_LITERAL_RE = re.compile(
    r"\b(?:\w+\.)?product_id\s*(=|IN)\s*\(?\s*(\d+(?:\s*,\s*\d+)*)(?:\s*\))?", re.I)


def repair_product_id_literal(sql: str, question: str):
    """Deterministic safety net: link_products_in_question may have resolved
    the product the question names, but the model sometimes ignores that and
    copies a literal product_id from a worked example in the prompt instead
    of the real one it was given. If the SQL's product_id doesn't match what
    was actually resolved, swap it in. Scoped to simple, single-table lookups
    (no JOIN) -- a multi-table query might use product_id for something else
    entirely, and blindly rewriting the first match found would be unsafe.
    Returns (sql, note-or-None)."""
    if re.search(r"\bJOIN\b", sql, re.I):
        return sql, None
    matches = link_products_in_question(question)
    if not matches:
        return sql, None
    correct_ids = sorted({m["product_id"] for m in matches})
    m = _PRODUCT_ID_LITERAL_RE.search(sql)
    if not m:
        return sql, None
    used_ids = sorted({int(x) for x in re.findall(r"\d+", m.group(2))})
    if used_ids == correct_ids:
        return sql, None
    if len(correct_ids) > 1:
        replacement = f"product_id IN ({', '.join(str(i) for i in correct_ids)})"
    else:
        replacement = f"product_id = {correct_ids[0]}"
    fixed = sql[:m.start()] + replacement + sql[m.end():]
    return fixed, f"product_id {used_ids} -> {correct_ids}"


def _ensure_product_name(sql: str) -> str:
    """A non-aggregate `SELECT price ... FROM products WHERE product_id IN (...)`
    over several products is unreadable without the name — add it so the answer
    can say which product each figure belongs to."""
    m = re.match(r"(?is)^\s*SELECT\s+(.*?)\s+FROM\s+products\b", sql)
    if not m:
        return sql
    cols = m.group(1)
    if "*" in cols or re.search(r"(?i)\bname\b", cols):
        return sql
    if re.search(r"(?i)\b(sum|count|avg|min|max)\s*\(|\bgroup\s+by\b", sql):
        return sql
    if not re.search(r"(?i)\b(product_id|name)\b", sql[m.end():]):
        return sql
    return sql[:m.start(1)] + "name, " + cols + sql[m.end(1):]

TOOL_SPEC = """You may call ONE tool before writing SQL:

  {"tool": "find_product", "query": "<product words from the question>"}

Call it whenever the question names a specific product (by name, brand, size, or
an approximate / misspelled name) and you need the exact catalogue name or id.
You will then be shown the matching rows (product_id, name, category, price,
current_stock); reply with the SQL using those exact name(s) or id(s). If the
tool returns no matches, write your best-guess SQL anyway.
Only call the tool once. For questions that don't name a product, skip it and
return the SQL directly."""


def format_tool_result(result: dict) -> str:
    import json
    if not result.get("matches"):
        return (f'find_product("{result["query"]}") -> no catalogue match. '
                f"Write best-guess SQL with a LIKE filter.")
    return (f'find_product("{result["query"]}") -> matches:\n'
            + json.dumps(result["matches"], indent=1, default=str)
            + "\nNow write the SQL using these exact product names or ids.")
