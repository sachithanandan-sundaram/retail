"""Mirror the SQLite demo DB into MongoDB so it can be browsed in Compass.

The query pipeline still runs on SQLite (retail.db) — this is a read-only
snapshot purely for visual inspection.

    python -m retail_llm.export_mongo
    # then open Compass -> mongodb://localhost:27017 -> database "retail_demo"
"""
import sys

from pymongo import MongoClient

from .config import DB_PATH
from .db import connect

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "retail_demo"
TABLES = ["products", "staff", "customers", "transactions", "transaction_items", "footfall"]


def main():
    if not DB_PATH.exists():
        sys.exit("retail.db not found — run `python -m retail_llm.generate_data` first")

    sconn = connect()
    mdb = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)[DB_NAME]
    mdb.client.admin.command("ping")

    for t in TABLES:
        rows = [dict(r) for r in sconn.execute(f"SELECT * FROM {t}")]
        mdb.drop_collection(t)
        if rows:
            mdb[t].insert_many(rows)
        print(f"  {t:20s} {len(rows):>8,d} docs")

    sconn.close()
    print(f"\nOpen Compass -> {MONGO_URI} -> database '{DB_NAME}'")


if __name__ == "__main__":
    main()
