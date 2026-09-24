"""Generic typo/spelling correction for the user's question text.

Product-name typos are already handled richly elsewhere (tools.resolve_product_names,
the find_product tool, repair_product_literals). This module covers everything
else a question can misspell: category names, retail domain nouns/verbs
(revenue, footfall, cashier, threshold, ...), exception types and staff roles.
Since the model now writes SQL for every question (no more fast-path regex
shortcuts), a misspelled domain word only has to survive one layer -- the
model's own tolerance for typos -- so this runs first to reduce that burden
and keep the question intent clear in the prompt.

Deliberately conservative: it only ever replaces a word with a close,
unambiguous match in a fixed retail vocabulary, and leaves alone anything
already spelled correctly, too short to judge, or a common English/query word
not specific to this domain.
"""
import difflib
import re

from .repair import CATEGORIES, CATEGORY_KEYWORDS, EXCEPTION_TYPES, ROLES

_DOMAIN_WORDS = {
    "revenue", "sales", "sale", "sell", "sold", "selling", "seller", "bill", "bills",
    "billing", "billed", "invoice", "invoices", "receipt", "receipts", "transaction",
    "transactions", "customer", "customers", "cashier", "cashiers", "counter",
    "staff", "employee", "employees", "manager", "managers", "footfall", "traffic",
    "visitor", "visitors", "discount", "exception", "exceptions", "threshold",
    "reorder", "category", "categories", "price", "pricing", "cost", "costs",
    "stock", "inventory", "quantity", "quantities", "product", "products",
    "margin", "profit", "percentage", "percent", "average", "growth", "increase",
    "decrease", "compared", "versus", "today", "yesterday", "week", "month",
    "quarter", "year", "hour", "peak", "busiest", "slowest", "fastest", "dead",
    "unsold", "void", "override", "voucher", "basket", "turnover", "spend",
    "spent", "purchase", "purchased", "weekday", "weekend",
}
for _c in CATEGORIES:
    _DOMAIN_WORDS.update(re.findall(r"[a-z]+", _c.lower()))
for _k in CATEGORY_KEYWORDS:
    _DOMAIN_WORDS.update(_k.split())
for _e in EXCEPTION_TYPES:
    _DOMAIN_WORDS.update(_e.lower().split("_"))
for _r in ROLES:
    _DOMAIN_WORDS.add(_r.lower())

# common words a retail question uses that must never be "corrected" into a
# domain word just because they happen to be close in edit distance.
_SAFE_WORDS = {
    "the", "a", "an", "of", "for", "is", "are", "was", "were", "be", "me",
    "please", "how", "much", "many", "what", "whats", "which", "does", "do",
    "did", "have", "has", "had", "this", "that", "these", "those", "and", "or",
    "to", "in", "on", "at", "by", "with", "from", "we", "you", "your", "i",
    "it", "us", "our", "my", "show", "tell", "give", "get", "list", "name",
    "names", "all", "each", "per", "than", "more", "less", "over", "under",
    "last", "next", "current", "total", "number", "items", "item", "units",
    "unit", "type", "types", "kind", "kinds", "option", "options", "variant",
    "variants", "brand", "brands", "size", "sizes", "colour", "color",
    "colours", "colors", "cheapest", "cheap", "costly", "expensive", "best",
    "worst", "top", "bottom", "highest", "lowest", "most", "least", "better",
    "worse", "good", "bad", "new", "old", "fresh", "similar", "related",
    "recent", "latest", "earliest", "first", "second", "third", "previous",
    "overall", "there", "them", "those", "who", "when", "where", "why",
    "can", "could", "would", "will", "should", "about", "into", "out",
    "just", "only", "also", "still", "yet", "not", "no", "yes", "any", "some",
    "day", "days", "hours", "minutes", "seconds", "time", "date", "dates",
    "rupees", "rupee", "rs", "inr", "did", "doing", "done",
}

_WORD_RE = re.compile(r"[A-Za-z]+")


def correct(question: str) -> str:
    """Best-effort per-word typo fix against the retail domain vocabulary."""
    if not question:
        return question

    def fix(m):
        word = m.group(0)
        lw = word.lower()
        if len(lw) < 5 or lw in _DOMAIN_WORDS or lw in _SAFE_WORDS:
            return word
        close = difflib.get_close_matches(lw, _DOMAIN_WORDS, n=1, cutoff=0.84)
        if not close:
            return word
        fixed = close[0]
        if abs(len(fixed) - len(lw)) > 2:
            return word
        if word[0].isupper():
            fixed = fixed.capitalize()
        return fixed

    return _WORD_RE.sub(fix, question)
