"""
Add search indexes used by structured tender retrieval.

Usage:
    python backend/scripts/add_tender_search_indexes.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import inspect, text

from app.db.session import engine


INDEXES = {
    "idx_tender_records_agency": "CREATE INDEX idx_tender_records_agency ON tender_records (agency)",
}


def main() -> None:
    inspector = inspect(engine)
    existing = {item["name"] for item in inspector.get_indexes("tender_records")}
    with engine.begin() as conn:
        for name, sql in INDEXES.items():
            if name in existing:
                print(f"[Index] {name}: exists")
                continue
            print(f"[Index] {name}: creating")
            conn.execute(text(sql))
            print(f"[Index] {name}: created")


if __name__ == "__main__":
    main()
