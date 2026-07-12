"""Check text_chunks status by domain."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT domain, COUNT(*) as total, "
        "SUM(CASE WHEN vector_indexed=1 THEN 1 ELSE 0 END) as indexed, "
        "SUM(CASE WHEN vector_indexed=0 THEN 1 ELSE 0 END) as not_indexed "
        "FROM text_chunks GROUP BY domain"
    ))
    print(f"{'domain':<15} {'total':>10} {'indexed':>10} {'not_indexed':>12}")
    print("-" * 50)
    for row in result:
        print(f"{row[0]:<15} {row[1]:>10} {row[2]:>10} {row[3]:>12}")
