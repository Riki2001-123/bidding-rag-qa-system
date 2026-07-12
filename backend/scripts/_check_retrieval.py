"""检查 Milvus 集合数据量 + text_chunks 数据量"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from app.db.session import SessionLocal
from app.models.entities import TextChunk
from app.services.vector_store import vector_store

# 1. 检查 MySQL text_chunks 数量（按域）
with SessionLocal() as db:
    for domain in ("policy", "tender", "enterprise"):
        count = db.scalar(
            select(func.count())
            .select_from(TextChunk)
            .where(TextChunk.domain == domain)
        )
        print(f"[MySQL] text_chunks {domain}: {count or 0} 条")

# 2. 检查 Milvus 集合数据量
try:
    for domain in ("policy", "tender", "enterprise"):
        client = vector_store._get_client()
        coll_name = f"rag_{domain}"
        stats = client.get_collection_stats(coll_name)
        row_count = stats.get("row_count", 0)
        print(f"[Milvus] {coll_name}: {row_count} 条向量")
except Exception as e:
    print(f"[Milvus] 查询失败: {e}")

# 3. 测试一次实际检索
print("\n--- 测试检索 ---")
with SessionLocal() as db:
    from app.models.entities import User
    admin = db.scalar(select(User).where(User.username == "admin").limit(1))
    if admin:
        from app.services.retrieval import search_domain
        test_questions = [
            ("tender", "政府采购招标公告"),
            ("enterprise", "袁江平贸易"),
            ("policy", "政府采购法"),
        ]
        for domain, q in test_questions:
            results = search_domain(db=db, domain=domain, user=admin, q=q, top_k=5)
            print(f"[检索] domain={domain}, q='{q}' -> {len(results)} 条结果")
            if results:
                for r in results[:2]:
                    print(f"  - {r.title[:50]} (score={r.score:.3f})")
