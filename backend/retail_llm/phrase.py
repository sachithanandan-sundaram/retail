"""Turn result rows into an English sentence — deterministically for fast paths,
and as a safety net over the model's phrasing for the LLM path.

Prompt instructions don't reliably stop a 3B model from emitting a bare "7",
inventing a number, or claiming "no data" over a non-empty result — so every
model answer is checked here and replaced with a deterministic sentence when it
fails.
"""
import re

# token-based: the column name is split on "_"/space. A strong-count token wins
# outright (footfall/units/seconds are never currency); otherwise a money token
# makes it currency. "total"/"bill" alone are ambiguous and count as neither.
_STRONG_COUNT_TOKENS = {
    "footfall", "count", "unit", "units", "qty", "quantity", "visit", "visits",
    "second", "seconds", "sec", "secs", "stock", "threshold", "pct", "percent",
    "day", "days", "hour", "hours", "rank", "id", "bills", "no", "number",
    "item", "items", "product", "products", "sku", "skus", "sold",
}
_MONEY_TOKENS = {
    "revenue", "amount", "billed", "value", "subtotal", "spend", "spent",
    "price", "cost", "turnover", "margin", "profit", "total", "bill",
}


_NO_DATA_RE = re.compile(r"no (matching )?data|no results|nothing (was )?found|"
                         r"couldn'?t find|there (are|were) no", re.I)


def _is_money_key(key: str) -> bool:
    tokens = set(re.split(r"[_\s]+", (key or "").lower()))
    if tokens & _STRONG_COUNT_TOKENS:
        return False
    return bool(tokens & _MONEY_TOKENS)


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})")


def money(v):
    try:
        return "Rs " + _in_grouping(round(float(v)))
    except (TypeError, ValueError):
        return str(v)


def _in_grouping(n: int) -> str:
    """Indian digit grouping: 1349364 -> '13,49,364'."""
    neg = n < 0
    s = str(abs(int(n)))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts) + "," + tail
    return ("-" if neg else "") + out


def preformat_for_prompt(rows: list) -> list:
    """Render money and timestamps as strings so the model copies them
    verbatim instead of re-deriving (and mangling) the digit grouping.
    Only feeds the phrasing prompt — the API's result table keeps raw values."""
    rows = _clean_result_keys(rows)
    out = []
    for row in rows:
        nr = {}
        for k, v in row.items():
            if v is not None and not isinstance(v, bool) and isinstance(v, (int, float)) \
                    and _is_money_key(k):
                nr[k] = money(v)
            elif isinstance(v, str) and _ISO_RE.match(v):
                try:
                    from datetime import datetime
                    nr[k] = datetime.fromisoformat(v).strftime("%d %b %Y %H:%M")
                except ValueError:
                    nr[k] = v
            else:
                nr[k] = v
        out.append(nr)
    return out


def _fmt(key, v):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        v = round(v, 2)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and _is_money_key(key):
        return money(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 1000:
        return f"{v:,}"
    return str(v)


def _humankey(k):
    return k.replace("_", " ")


_SQLEXPR_RE = re.compile(r"[()]|^\s*(count|sum|avg|min|max|round|total)\b", re.I)


def _clean_result_keys(rows: list) -> list:
    """The small model sometimes leaves an aggregate unaliased, so a row key is
    a raw SQL expression like 'COUNT(DISTINCT t2.product_id)'. Rename such keys
    to a readable word before it reaches the phrasing step."""
    def clean(k):
        if not _SQLEXPR_RE.search(k):
            return k
        kl = k.lower()
        if "count" in kl:
            return "count"
        if "avg" in kl:
            return "average"
        if "sum" in kl or "total" in kl or "round" in kl:
            return "total"
        return "value"
    out = []
    for row in rows:
        seen, nr = set(), {}
        for k, v in row.items():
            nk = clean(k)
            while nk in seen:
                nk += "_2"
            seen.add(nk)
            nr[nk] = v
        out.append(nr)
    return out


_YESNO_RE = re.compile(r"^\s*(do|does|are)\s+(we|you|they)\b.*\b(sell|stock|carry|have|"
                       r"stocking|selling|carrying)\b", re.I)
_HOWMANY_TYPES_RE = re.compile(
    r"\bhow many\s+(?:types?|kinds?|variants?|varieties|different|distinct|unique|options?)\b"
    r"|\b(?:list|what|which)\b.*\b(?:types?|kinds?|variants?|varieties)\s+of\b", re.I)


def rows_to_sentence(rows: list, question: str = "") -> str:
    if not rows:
        if _YESNO_RE.match(question):
            return "No — that isn't in the catalogue."
        return "Nothing matched that query."
    rows = _clean_result_keys(rows)

    # "how many types / list the kinds of X" — the answer is the ROW COUNT,
    # and the small model keeps grabbing a stray column value instead.
    if _HOWMANY_TYPES_RE.search(question) and "name" in rows[0]:
        names = [r["name"] for r in rows[:12]]
        more = "" if len(rows) <= 12 else f" (+{len(rows) - 12} more)"
        noun = "type" if len(rows) == 1 else "types"
        return f"{len(rows)} {noun}: {', '.join(names)}{more}."

    # "do we sell/stock/carry X"
    if _YESNO_RE.match(question):
        if "name" in rows[0]:
            names = [r["name"] for r in rows[:8]]
            more = "" if len(rows) <= 8 else f" (+{len(rows) - 8} more)"
            if len(names) == 1:
                return f"Yes — we carry {names[0]}."
            return f"Yes — {len(rows)} in the catalogue: {', '.join(names)}{more}."
        # a bare count row
        vals = [v for v in rows[0].values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(rows) == 1 and vals:
            return "No — that isn't in the catalogue." if vals[0] == 0 else f"Yes — {vals[0]} in the catalogue."

    # a bill (header columns repeated across one row per line item) -- only
    # trust this shape if every row is actually the SAME bill; a small model's
    # unfiltered join (no transaction_id restriction) can return line items
    # from many different bills, and formatting that as "one bill" would
    # silently misattribute other customers' items and totals.
    if "bill_no" in rows[0] and "item" in rows[0] and \
       all(r.get("bill_no") == rows[0].get("bill_no") for r in rows):
        h = rows[0]
        when = h.get("billed_at") or h.get("ts") or ""
        try:
            from datetime import datetime
            when = datetime.fromisoformat(when).strftime("%d %b %Y %H:%M")
        except (ValueError, TypeError):
            pass
        items = "; ".join(
            f"{r['item']} ×{r['quantity']} ({money(r['line_total'])})" for r in rows)
        who = f" for {h['customer']}" if h.get("customer") else " (walk-in)"
        exc = f" — flagged {h['exception_type']}" if h.get("exception_type") else ""
        return (f"Bill #{h['bill_no']} — {h.get('cashier', 'n/a')}{who}, {when}, "
                f"{h.get('bill_seconds', '?')}s. {len(rows)} item(s): {items}. "
                f"Total {money(h.get('total_amount'))}{exc}.")

    if len(rows) == 1:
        row = rows[0]
        parts = [f"{_humankey(k)}: {_fmt(k, v)}" for k, v in row.items()]
        if len(parts) <= 4:
            return _capitalize("; ".join(parts) + ".")
        # a wide single row (e.g. a customer profile) — lead with a name/id
        head = row.get("name") or row.get("customer") or row.get("cashier") or \
            f"row {row.get(next(iter(row)))}"
        rest = "; ".join(parts[:6])
        return f"{head} — {rest}."

    # a "this period vs previous period" comparison (period column = the label)
    if len(rows) == 2 and any("period" in k.lower() for k in rows[0]):
        pk = next(k for k in rows[0] if "period" in k.lower())
        vk = next((k for k in rows[0] if k != pk and isinstance(rows[0][k], (int, float))
                   and not isinstance(rows[0][k], bool)), None)
        if vk:
            cur, prev = rows[0], rows[1]
            cv, pv = cur.get(vk) or 0, prev.get(vk) or 0
            delta = ((cv - pv) / pv * 100) if pv else 0
            arrow = "up" if cv >= pv else "down"
            return (f"{_fmt(vk, cv)} this period vs {_fmt(vk, pv)} the period before "
                    f"({arrow} {abs(delta):.0f}%).")

    # multi-row: "<label> (<value>), ..." for the leading rows. Label = first
    # text column; value = the most answer-like numeric column (a money/metric
    # name beats a bare id/timestamp).
    keys = list(rows[0].keys())
    label_key = next((k for k in keys if isinstance(rows[0][k], str)), keys[0])
    _num = [k for k in keys if isinstance(rows[0][k], (int, float)) and not isinstance(rows[0][k], bool)]
    _METRIC = re.compile(r"revenue|amount|billed|value|total|subtotal|spend|price|units?|"
                         r"sold|qty|quantity|count|footfall|seconds?|avg|average|stock|bills?", re.I)
    _ID = re.compile(r"(^|_)id$|transaction|number|^no$", re.I)
    value_key = next((k for k in _num if _METRIC.search(k) and not _ID.search(k)), None) \
        or next((k for k in _num if not _ID.search(k)), None) \
        or (_num[0] if _num else None)
    shown = rows[:5]
    if value_key:
        items = "; ".join(f"{r.get(label_key, '?')} ({_fmt(value_key, r.get(value_key))})"
                          for r in shown)
        metric = _humankey(value_key)
        if len(rows) > len(shown):
            return f"Top {len(shown)} of {len(rows)} by {metric} — {items}."
        return f"By {metric}: {items}."
    items = "; ".join(str(r.get(label_key, "?")) for r in shown)
    return f"{len(rows)} results: {items}" + ("…" if len(rows) > len(shown) else ".")


def _capitalize(s):
    return s[:1].upper() + s[1:] if s else s


def _is_bare(answer: str) -> bool:
    return len(re.findall(r"[A-Za-z]{2,}", answer)) < 2


def _looks_generic(answer: str, rows: list) -> bool:
    """A last-ditch check for a non-answer: the model returned prose that
    references none of the result at all. Deliberately lenient — it only
    fires when the answer shares *nothing* with the rows (no number, no
    name), so legitimate rephrasings ('Rs 1.35 lakh', '20 Feb 2026') are
    never clobbered."""
    a = answer.lower()
    has_number_row = has_string_row = False
    for row in rows[:5]:
        for v in row.values():
            if v is None:
                continue
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                has_number_row = True
            elif isinstance(v, str) and len(v) > 2:
                has_string_row = True
                if v.lower() in a:
                    return False
                # a word from a multi-word value (product/customer name)
                if any(w for w in re.findall(r"[a-z]{4,}", v.lower()) if w in a):
                    return False
    if has_number_row and re.search(r"\d", answer):
        return False
    if not has_number_row and not has_string_row:
        return False
    return True


def _invents_big_number(answer: str, rows: list) -> bool:
    """The model computing its own cross-row total ("total revenue by category
    is Rs 2,01,584") — that figure appears in no row and is usually wrong.
    A thousands-separated figure named in the answer must trace to a real value.

    Only flags comma-grouped tokens ("2,01,584" / "35,192") — those are always
    the model reporting a money figure. Bare digit runs are left alone so a year
    ("2026"), a phone number, an id or a time never trip this."""
    present = set()
    for row in rows:
        for v in row.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                present.add(round(v))
    # also accept sums the model might legitimately state for a 2-3 row compare
    nums = [round(v) for row in rows for v in row.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if 2 <= len(nums) <= 6:
        present.add(sum(nums))
    for tok in re.findall(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?", answer):
        n = round(float(tok.replace(",", "")))
        if n >= 1000 and not any(abs(n - p) <= 2 for p in present):
            return True
    return False


def _dedupe_sentences(answer: str) -> str:
    """Small models (esp. Llama-3.2-3B) pad an answer with the same fact
    restated: "... is Rs 342. It is priced at Rs 342." Drop a later sentence
    that introduces no new number and no new significant noun, and cap at 3."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]
    kept, seen_nums, seen_words = [], set(), set()
    for s in sents:
        nums = set(re.findall(r"\d[\d,]*", s))
        words = set(re.findall(r"[A-Za-z]{4,}", s.lower())) - {
            "this", "that", "there", "which", "with", "from", "have", "were",
            "also", "only", "available", "these", "those", "total", "amount"}
        new_nums = nums - seen_nums
        new_words = words - seen_words
        if kept and not new_nums and len(new_words) < 2:
            continue
        kept.append(s)
        seen_nums |= nums
        seen_words |= words
        if len(kept) >= 3:
            break
    return " ".join(kept)


def finalize(rows: list, answer: str, question: str = "") -> str:
    answer = _dedupe_sentences((answer or "").strip())
    if not rows:
        return "Nothing matched that query."
    rows = _clean_result_keys(rows)
    if _is_bare(answer):
        return rows_to_sentence(rows, question)
    if _NO_DATA_RE.search(answer):          # rows exist -> this is a hallucination
        return rows_to_sentence(rows, question)
    if _looks_generic(answer, rows):
        return rows_to_sentence(rows, question)
    if _invents_big_number(answer, rows):
        return rows_to_sentence(rows, question)
    return answer
