import sqlite3
from .config import DB_PATH

SCHEMA_DDL = """
CREATE TABLE products (
    product_id        INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    category          TEXT NOT NULL,
    price             REAL NOT NULL,
    cost              REAL NOT NULL,
    reorder_threshold INTEGER NOT NULL,
    current_stock     INTEGER NOT NULL,
    date_added        TEXT NOT NULL,   -- ISO date
    last_sold_date    TEXT             -- ISO date, NULL if never sold
);

CREATE TABLE staff (
    staff_id  INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    role      TEXT NOT NULL,           -- CASHIER, FLOOR, MANAGER
    hire_date TEXT NOT NULL
);

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT,
    first_visit TEXT NOT NULL,
    last_visit  TEXT NOT NULL,
    visit_count INTEGER NOT NULL,
    total_spend REAL NOT NULL
);

CREATE TABLE transactions (
    transaction_id INTEGER PRIMARY KEY,
    ts             TEXT NOT NULL,      -- ISO datetime, bill completion time
    cashier_id     INTEGER NOT NULL REFERENCES staff(staff_id),
    customer_id    INTEGER REFERENCES customers(customer_id),
    subtotal       REAL NOT NULL,
    discount_pct   REAL NOT NULL DEFAULT 0,
    total_amount   REAL NOT NULL,
    is_exception   INTEGER NOT NULL DEFAULT 0,
    exception_type TEXT,               -- VOID_WITHOUT_SCAN, HIGH_DISCOUNT, MANUAL_PRICE_OVERRIDE, NO_SALE_OPEN
    start_time     TEXT NOT NULL,      -- ISO datetime, when the bill started
    end_time       TEXT NOT NULL,      -- ISO datetime, == ts
    bill_seconds   INTEGER NOT NULL    -- end_time - start_time, seconds
);

CREATE TABLE transaction_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(transaction_id),
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    quantity       INTEGER NOT NULL,
    unit_price     REAL NOT NULL,
    line_total     REAL NOT NULL
);

CREATE TABLE footfall (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id  INTEGER NOT NULL,
    ts        TEXT NOT NULL,           -- ISO datetime, start of the hour bucket
    count     INTEGER NOT NULL         -- people entering during that hour
);

CREATE INDEX idx_tx_ts ON transactions(ts);
CREATE INDEX idx_tx_cashier ON transactions(cashier_id);
CREATE INDEX idx_ti_tx ON transaction_items(transaction_id);
CREATE INDEX idx_ti_product ON transaction_items(product_id);
CREATE INDEX idx_footfall_ts ON footfall(ts);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_readonly(sql: str, params=()):
    """Execute a single read-only SELECT and return list[dict]."""
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
