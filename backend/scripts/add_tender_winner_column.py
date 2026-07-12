"""
Add and backfill tender_records.winner.

Usage:
    python backend/scripts/add_tender_winner_column.py --dry-run
    python backend/scripts/add_tender_winner_column.py

The script is idempotent. It adds the winner column when missing, then
best-effort extracts winner/supplier names from existing text fields.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import inspect, text

from app.db.session import SessionLocal, engine


WINNER_PATTERNS = (
    r"(?:中标(?:人|单位|供应商|方)|成交(?:人|单位|供应商|方)|供应商名称)\s*[：:]\s*([^；;，,\n\r]+)",
    r"(?:中标(?:人|单位|供应商|方)|成交(?:人|单位|供应商|方))\s*为\s*([^；;，,\n\r]+)",
)


def clean_winner(value: str) -> str:
    text_value = (value or "").strip()
    text_value = re.sub(r"\s+", "", text_value)
    text_value = text_value.strip(" ：:。；;，,、[]【】（）()")
    text_value = re.split(r"(?:中标金额|成交金额|金额|报价|地址|统一社会信用代码|联系方式)", text_value)[0]
    return text_value[:255]


def extract_winner(row: Mapping[str, Any]) -> Optional[str]:
    fields = [
        row.get("content_summary"),
        row.get("title"),
        row.get("project_name"),
        row.get("source_file_name"),
    ]
    haystack = "\n".join(str(field) for field in fields if field)
    for pattern in WINNER_PATTERNS:
        match = re.search(pattern, haystack)
        if match:
            candidate = clean_winner(match.group(1))
            if len(candidate) >= 2:
                return candidate
    return None


def ensure_column(dry_run: bool) -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("tender_records")}
    if "winner" in columns:
        print("[OK] tender_records.winner already exists")
        return
    sql = "ALTER TABLE tender_records ADD COLUMN winner VARCHAR(255) NULL, ADD INDEX ix_tender_records_winner (winner)"
    if dry_run:
        print(f"[DRY-RUN] {sql}")
        return
    with engine.begin() as conn:
        conn.execute(text(sql))
    print("[OK] added tender_records.winner")


def backfill(dry_run: bool, batch_size: int) -> None:
    scanned = updated = parsed = 0
    with SessionLocal() as db:
        rows = db.execute(
            text(
                "SELECT id, title, project_name, content_summary, source_file_name "
                "FROM tender_records WHERE winner IS NULL OR winner = ''"
            )
        ).mappings()
        pending = []
        for row in rows:
            scanned += 1
            winner = extract_winner(row)
            if not winner:
                continue
            parsed += 1
            pending.append({"id": row["id"], "winner": winner})
            if len(pending) >= batch_size:
                updated += flush_updates(db, pending, dry_run)
                pending = []
        if pending:
            updated += flush_updates(db, pending, dry_run)
        if not dry_run:
            db.commit()
    print(f"[OK] scanned={scanned}, parsed={parsed}, updated={updated}, dry_run={dry_run}")


def flush_updates(db, rows, dry_run: bool) -> int:
    if dry_run:
        for row in rows[:5]:
            print(f"[DRY-RUN] id={row['id']} winner={row['winner']}")
        return len(rows)
    db.execute(
        text("UPDATE tender_records SET winner = :winner WHERE id = :id"),
        rows,
    )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Add and backfill tender_records.winner")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without changing the database")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    ensure_column(args.dry_run)
    if not args.dry_run or "winner" in {column["name"] for column in inspect(engine).get_columns("tender_records")}:
        backfill(args.dry_run, args.batch_size)


if __name__ == "__main__":
    main()
