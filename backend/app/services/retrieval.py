import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.entities import Attachment, EnterpriseRecord, PolicyRecord, TenderRecord, TextChunk, User, UserProjectGrant
from app.services.bm25_retriever import bm25_index
from app.services.embeddings import embedding_service
from app.services.reranker import reranker_service
from app.services.vector_store import vector_store


DOMAIN_MODEL_MAP = {"policy": PolicyRecord, "tender": TenderRecord, "enterprise": EnterpriseRecord}

# BM25 + 向量检索的权重（可调节）
BM25_WEIGHT = 0.3
BM25_WEIGHT = 0.3
VECTOR_WEIGHT = 0.7
HIGH_RECALL_VECTOR_TOP_K = 200
HIGH_RECALL_BM25_TOP_K = 200
HIGH_RECALL_KEYWORD_TOP_K = 200
HIGH_RECALL_STRUCTURED_TOP_K = 50
HIGH_RECALL_PRE_RERANK_RECORDS = 80
BM25_MAX_CHUNKS = 50000
RRF_K = 60
SOURCE_WEIGHTS = {
    "vector": 1.0,
    "bm25": 1.0,
    "keyword": 0.8,
    "structured": 1.5,
}


def escape_like(value: str) -> str:
    """转义 SQL LIKE 通配符（%、_、\\），防止通配符注入。"""
    return re.sub(r"([%_\\])", r"\\\1", value)


def extract_relevant_snippet(text: str, query: str, window: int = 260) -> str:
    """Return a compact snippet around question keywords when possible."""
    source = (text or "").strip()
    if not source:
        return ""

    keywords = _extract_snippet_keywords(query)
    positions = [source.find(keyword) for keyword in keywords if keyword and source.find(keyword) >= 0]
    if positions:
        center = min(positions)
        start = max(center - window // 2, 0)
        end = min(start + window, len(source))
        snippet = source[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(source):
            snippet += "..."
        return snippet
    return source[:window]


def _extract_snippet_keywords(query: str) -> List[str]:
    q = query or ""
    keywords = re.findall(r"第[一二三四五六七八九十百千万零〇两\d]+条", q)
    keywords.extend(re.findall(r"\d+\s*个工作日|\d+\s*工作日", q))
    keywords.extend(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,}", q))
    stopwords = {
        "哪些类型", "情况下", "可以", "对于", "原则上", "不超过",
        "多少个", "有什么", "相比", "不同", "一个企业",
    }
    deduped: List[str] = []
    for keyword in keywords:
        normalized = keyword.replace(" ", "")
        if normalized in stopwords or normalized in deduped:
            continue
        deduped.append(normalized)
    return deduped


@dataclass
class RetrievedItem:
    domain: str
    record_id: int
    title: str
    score: float
    summary: str
    publish_date: Any
    key_fields: dict
    source_fields: List[str]


@dataclass
class HighRecallSearchResult:
    items: List[RetrievedItem]
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _RecordCandidate:
    record_id: int
    score: float = 0.0
    sources: List[str] = field(default_factory=list)
    source_fields: List[str] = field(default_factory=list)
    chunk_ids: List[int] = field(default_factory=list)


def get_allowed_project_ids(db: Session, user: User) -> List[int]:
    if user.role == "admin":
        return []
    grants = db.scalars(select(UserProjectGrant.project_id).where(UserProjectGrant.user_id == user.id)).all()
    return list(grants)


def apply_permission_filters(query, model, db: Session, user: User):
    if user.role == "admin":
        return query
    allowed_project_ids = get_allowed_project_ids(db, user)
    if user.role == "supplier":
        query = query.where(model.access_level == "public")
    else:
        query = query.where(model.access_level.in_(["public", "internal"]))
    if allowed_project_ids:
        query = query.where(or_(model.project_id.is_(None), model.project_id.in_(allowed_project_ids)))
    else:
        query = query.where(model.project_id.is_(None))
    return query


def search_domain(
    db: Session,
    domain: str,
    user: User,
    q: Optional[str] = None,
    project_id: Optional[int] = None,
    top_k: int = 10,
    **filters,
) -> List[RetrievedItem]:
    if q and project_id is None and not filters:
        return high_recall_search(db=db, domain=domain, user=user, query_text=q, top_k=top_k).items

    model = DOMAIN_MODEL_MAP[domain]
    stmt = select(model)
    stmt = apply_permission_filters(stmt, model, db, user)
    if project_id is not None:
        stmt = stmt.where(model.project_id == project_id)

    if domain == "policy":
        if filters.get("region"):
            stmt = stmt.where(model.region.ilike(f"%{escape_like(filters['region'])}%", escape="\\"))
        if q:
            safe_q = escape_like(q)
            stmt = stmt.where(or_(
                model.title.ilike(f"%{safe_q}%", escape="\\"),
                model.summary.ilike(f"%{safe_q}%", escape="\\"),
                model.content.ilike(f"%{safe_q}%", escape="\\"),
            ))
    elif domain == "tender":
        if filters.get("stage"):
            stmt = stmt.where(model.stage.ilike(f"%{escape_like(filters['stage'])}%", escape="\\"))
        if filters.get("tenderer"):
            stmt = stmt.where(model.tenderer.ilike(f"%{escape_like(filters['tenderer'])}%", escape="\\"))
        if filters.get("region"):
            stmt = stmt.where(model.region.ilike(f"%{escape_like(filters['region'])}%", escape="\\"))
        if q:
            safe_q = escape_like(q)
            stmt = stmt.where(or_(
                model.title.ilike(f"%{safe_q}%", escape="\\"),
                model.project_name.ilike(f"%{safe_q}%", escape="\\"),
                model.content_summary.ilike(f"%{safe_q}%", escape="\\"),
            ))
    elif domain == "enterprise":
        if filters.get("region"):
            stmt = stmt.where(model.region.ilike(f"%{escape_like(filters['region'])}%", escape="\\"))
        if filters.get("industry"):
            stmt = stmt.where(model.industry.ilike(f"%{escape_like(filters['industry'])}%", escape="\\"))
        if q:
            safe_q = escape_like(q)
            stmt = stmt.where(
                or_(
                    model.enterprise_name.ilike(f"%{safe_q}%", escape="\\"),
                    model.unified_social_code.ilike(f"%{safe_q}%", escape="\\"),
                    model.business_scope.ilike(f"%{safe_q}%", escape="\\"),
                    model.remark.ilike(f"%{safe_q}%", escape="\\"),
                )
            )
    rows = db.scalars(stmt.limit(top_k)).all()
    items = [_to_retrieved_item(domain, row, score=1.0, source_fields=["structured"]) for row in rows]
    if q:
        items = merge_with_semantic_hits(db, domain, user, q, items, top_k)
    return items[:top_k]


def high_recall_search(
    db: Session,
    domain: str,
    user: User,
    query_text: str,
    top_k: int = 20,
    *,
    vector_top_k: int = HIGH_RECALL_VECTOR_TOP_K,
    bm25_top_k: int = HIGH_RECALL_BM25_TOP_K,
    keyword_top_k: int = HIGH_RECALL_KEYWORD_TOP_K,
    structured_top_k: int = HIGH_RECALL_STRUCTURED_TOP_K,
    pre_rerank_records: int = HIGH_RECALL_PRE_RERANK_RECORDS,
    use_reranker: bool = True,
) -> HighRecallSearchResult:
    candidates: Dict[int, _RecordCandidate] = {}
    diagnostics: Dict[str, Any] = {
        "sources": defaultdict(int),
        "errors": [],
        "keywords": [],
        "sources_by_record": {},
        "chunk_ids_by_record": {},
    }

    def add_chunk_hits(hits: Sequence[dict], source: str) -> None:
        for rank, hit in enumerate(hits, start=1):
            add_rrf_candidate(
                candidates,
                diagnostics,
                int(hit.get("record_id") or 0),
                source,
                rank,
                source_fields=[hit.get("source_field") or source],
                chunk_id=int(hit.get("chunk_id")) if hit.get("chunk_id") is not None else None,
            )

    if domain == "policy":
        try:
            vector = embedding_service.embed_query(query_text)
            vector_hits = vector_store.search(domain, vector, top_k=vector_top_k)
            add_chunk_hits(vector_hits, "vector")
        except Exception as exc:
            diagnostics["errors"].append(f"vector: {exc}")

        try:
            ensure_bm25_index(db, domain)
            bm25_hits = bm25_index.search(domain, query_text, top_k=bm25_top_k) if bm25_index.available else []
            add_chunk_hits(_hydrate_chunk_hits(db, bm25_hits), "bm25")
        except Exception as exc:
            diagnostics["errors"].append(f"bm25: {exc}")

    if domain == "policy":
        try:
            keywords = extract_retrieval_keywords(query_text)
            diagnostics["keywords"] = keywords
            keyword_hits = keyword_chunk_search(db, domain, keywords, top_k=keyword_top_k)
            add_chunk_hits(keyword_hits, "keyword")
        except Exception as exc:
            diagnostics["errors"].append(f"keyword: {exc}")

    try:
        structured_records = structured_record_candidates(db, domain, user, query_text, structured_top_k)
        for rank, item in enumerate(structured_records, start=1):
            add_rrf_candidate(
                candidates,
                diagnostics,
                item["record_id"],
                "structured",
                rank,
                source_fields=item.get("source_fields") or ["structured"],
            )
    except Exception as exc:
        diagnostics["errors"].append(f"structured: {exc}")

    ranked_candidates = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
    permitted_items = _candidates_to_items(db, domain, user, ranked_candidates)
    preliminary = permitted_items[:max(pre_rerank_records, top_k)]
    final_items = rerank_retrieved_items(query_text, preliminary, top_k, use_reranker=use_reranker)

    for item in final_items:
        candidate = candidates.get(item.record_id)
        if candidate:
            diagnostics["sources_by_record"][str(item.record_id)] = list(candidate.sources)
            diagnostics["chunk_ids_by_record"][str(item.record_id)] = list(candidate.chunk_ids)
    diagnostics["sources"] = dict(diagnostics["sources"])
    return HighRecallSearchResult(items=final_items, diagnostics=diagnostics)


def add_rrf_candidate(
    candidates: Dict[int, _RecordCandidate],
    diagnostics: Dict[str, Any],
    record_id: int,
    source: str,
    rank: int,
    *,
    source_fields: Optional[Sequence[str]] = None,
    chunk_id: Optional[int] = None,
) -> None:
    if not record_id:
        return
    candidate = candidates.setdefault(record_id, _RecordCandidate(record_id=record_id))
    candidate.score += SOURCE_WEIGHTS.get(source, 1.0) / (RRF_K + max(rank, 1))
    if source not in candidate.sources:
        candidate.sources.append(source)
    for field_name in source_fields or []:
        if field_name and field_name not in candidate.source_fields:
            candidate.source_fields.append(field_name)
    if chunk_id is not None and chunk_id not in candidate.chunk_ids:
        candidate.chunk_ids.append(chunk_id)
    sources = diagnostics.setdefault("sources", defaultdict(int))
    sources[source] = sources.get(source, 0) + 1


def ensure_bm25_index(db: Session, domain: str) -> None:
    if not bm25_index.available or domain in bm25_index._indices:
        return
    total = db.scalar(select(func.count(TextChunk.id)).where(TextChunk.domain == domain)) or 0
    if total > BM25_MAX_CHUNKS:
        print(f"[BM25] skip building index for {domain}: {total} chunks exceeds {BM25_MAX_CHUNKS}", flush=True)
        return
    chunks = db.scalars(select(TextChunk).where(TextChunk.domain == domain)).all()
    bm25_index.build_index(domain, list(chunks))


def _hydrate_chunk_hits(db: Session, hits: Sequence[dict]) -> List[dict]:
    chunk_ids = [int(hit["chunk_id"]) for hit in hits if hit.get("chunk_id") is not None]
    if not chunk_ids:
        return []
    chunks = db.scalars(select(TextChunk).where(TextChunk.id.in_(chunk_ids))).all()
    chunk_map = {chunk.id: chunk for chunk in chunks}
    hydrated = []
    for hit in hits:
        chunk = chunk_map.get(int(hit["chunk_id"]))
        if not chunk:
            continue
        hydrated.append({
            "chunk_id": chunk.id,
            "record_id": chunk.record_id,
            "source_field": chunk.source_field,
            "score": float(hit.get("score", 0.0)),
        })
    return hydrated


def extract_retrieval_keywords(query_text: str, max_keywords: int = 12) -> List[str]:
    text = (query_text or "").strip()
    if not text:
        return []

    keywords: List[str] = []
    for keyword in _extract_snippet_keywords(text):
        _append_keyword(keywords, keyword)

    parts = re.split(r"[，。！？；、,.!?;\s]+", text)
    for part in parts:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", part)
        if len(cleaned) >= 4:
            _append_keyword(keywords, cleaned[:24])

    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    for size in (12, 10, 8, 6, 4):
        if len(compact) < size:
            continue
        step = max(size // 2, 2)
        for start in range(0, len(compact) - size + 1, step):
            _append_keyword(keywords, compact[start:start + size])
            if len(keywords) >= max_keywords:
                return keywords[:max_keywords]
    return keywords[:max_keywords]


def _append_keyword(keywords: List[str], value: str) -> None:
    cleaned = (value or "").strip()
    if len(cleaned) < 2:
        return
    if cleaned not in keywords:
        keywords.append(cleaned)


def keyword_chunk_search(db: Session, domain: str, keywords: Sequence[str], top_k: int = HIGH_RECALL_KEYWORD_TOP_K) -> List[dict]:
    hits = []
    seen = set()
    per_keyword = max(top_k // max(len(keywords), 1), 10)
    for keyword in keywords:
        safe = escape_like(keyword)
        stmt = (
            select(TextChunk)
            .where(TextChunk.domain == domain, TextChunk.content.ilike(f"%{safe}%", escape="\\"))
            .limit(per_keyword)
        )
        for chunk in db.scalars(stmt).all():
            if chunk.id in seen:
                continue
            seen.add(chunk.id)
            hits.append({
                "chunk_id": chunk.id,
                "record_id": chunk.record_id,
                "source_field": chunk.source_field,
                "score": 1.0,
            })
            if len(hits) >= top_k:
                return hits
    return hits


def structured_record_candidates(db: Session, domain: str, user: User, query_text: str, top_k: int) -> List[dict]:
    if domain not in ("tender", "enterprise"):
        return []
    from app.services.sql_agent import detect_sql_intent, execute_sql_intent
    from app.services.structured_retrieval import retrieve_structured

    output: List[dict] = []
    seen = set()

    intent = detect_sql_intent(query_text, domain)
    if intent:
        result = execute_sql_intent(db, user, intent, query_text)
        if result and result.success:
            for citation in result.citations or []:
                record_id = int(citation.get("record_id") or 0)
                if record_id and record_id not in seen:
                    seen.add(record_id)
                    output.append({"record_id": record_id, "source_fields": citation.get("source_fields") or ["sql"]})

    structured = retrieve_structured(db, user, domain, query_text, top_k=top_k)
    if structured.success:
        for citation in structured.citations:
            record_id = int(citation.record_id)
            if record_id and record_id not in seen:
                seen.add(record_id)
                output.append({"record_id": record_id, "source_fields": citation.source_fields or ["structured"]})

    return output[:top_k]


def _candidates_to_items(db: Session, domain: str, user: User, candidates: Sequence[_RecordCandidate]) -> List[RetrievedItem]:
    record_ids = [candidate.record_id for candidate in candidates]
    if not record_ids:
        return []
    model = DOMAIN_MODEL_MAP[domain]
    records = db.scalars(select(model).where(model.id.in_(record_ids))).all()
    record_map = {record.id: record for record in records}
    allowed_project_ids = get_allowed_project_ids(db, user)
    items = []
    for candidate in candidates:
        record = record_map.get(candidate.record_id)
        if not record or not _check_record_permission(record, user, allowed_project_ids):
            continue
        source_fields = candidate.source_fields or ["high_recall"]
        item = _to_retrieved_item(domain, record, score=candidate.score, source_fields=source_fields)
        items.append(item)
    return items


def rerank_retrieved_items(
    query_text: str,
    items: Sequence[RetrievedItem],
    top_k: int,
    *,
    use_reranker: bool = True,
) -> List[RetrievedItem]:
    if not use_reranker or not reranker_service.enabled or len(items) <= 1:
        return list(items[:top_k])
    passages = []
    for item in items:
        parts = [item.title]
        if item.summary:
            parts.append(item.summary)
        passages.append(" | ".join(parts))
    rerank_results = reranker_service.rerank(query_text, passages, top_k=min(top_k, len(items)))
    return [items[result["index"]] for result in rerank_results]


def _check_record_permission(record, user: User, allowed_project_ids: List[int]) -> bool:
    """在 Python 层面检查单条记录的权限（配合批量查询使用）。"""
    if user.role == "admin":
        return True
    # access_level 检查
    if user.role == "supplier":
        if getattr(record, "access_level", None) != "public":
            return False
    else:
        if getattr(record, "access_level", None) not in ("public", "internal"):
            return False
    # project_id 检查
    record_pid = getattr(record, "project_id", None)
    if record_pid is not None:
        return record_pid in allowed_project_ids
    return True


def merge_with_semantic_hits(db: Session, domain: str, user: User, query_text: str, items: List[RetrievedItem], top_k: int) -> List[RetrievedItem]:
    """混合检索：BM25 关键词检索 + 向量语义检索，结果合并排序。"""
    model = DOMAIN_MODEL_MAP[domain]

    # 1) 向量语义检索
    vector = embedding_service.embed_query(query_text)
    try:
        semantic_hits = vector_store.search(domain, vector, top_k=top_k * 2)
    except MemoryError as exc:
        print(f"[Retrieval] semantic search skipped for {domain}: {exc}")
        semantic_hits = []
    except Exception as exc:
        print(f"[Retrieval] semantic search failed for {domain}: {exc}")
        semantic_hits = []

    # 2) BM25 关键词检索
    bm25_hits = []
    if bm25_index.available:
        # 按需构建 BM25 索引（懒加载）
        if domain not in bm25_index._indices:
            chunks = db.scalars(
                select(TextChunk).where(TextChunk.domain == domain)
            ).all()
            bm25_index.build_index(domain, list(chunks))
        bm25_hits = bm25_index.search(domain, query_text, top_k=top_k * 2)

    if not semantic_hits and not bm25_hits:
        return items

    # 合并所有命中的 chunk_id 及其分数
    # score_map: chunk_id -> { "vector_score": x, "bm25_score": y }
    score_map: dict = {}
    for hit in semantic_hits:
        cid = hit["chunk_id"]
        if cid not in score_map:
            score_map[cid] = {"vector_score": 0, "bm25_score": 0}
        score_map[cid]["vector_score"] = float(hit["score"])

    for hit in bm25_hits:
        cid = hit["chunk_id"]
        if cid not in score_map:
            score_map[cid] = {"vector_score": 0, "bm25_score": 0}
        score_map[cid]["bm25_score"] = float(hit["score"])

    # 归一化分数并加权融合
    all_vector = [v["vector_score"] for v in score_map.values()]
    all_bm25 = [v["bm25_score"] for v in score_map.values()]
    max_vector = max(all_vector) if all_vector else 1.0
    max_bm25 = max(all_bm25) if all_bm25 else 1.0

    # 加权融合分数
    fused_scores = {}
    for cid, scores in score_map.items():
        norm_v = scores["vector_score"] / max_vector if max_vector > 0 else 0
        norm_b = scores["bm25_score"] / max_bm25 if max_bm25 > 0 else 0
        fused_scores[cid] = VECTOR_WEIGHT * norm_v + BM25_WEIGHT * norm_b

    # 按 fused score 排序，取 top_k * 2 候选
    ranked_ids = sorted(fused_scores.keys(), key=lambda c: fused_scores[c], reverse=True)[:top_k * 2]

    # 查询 TextChunk 和关联的 record
    chunk_ids = list(ranked_ids)
    chunk_map = {chunk.id: chunk for chunk in db.scalars(select(TextChunk).where(TextChunk.id.in_(chunk_ids))).all()}

    item_map = {item.record_id: item for item in items}

    # 收集所有需要查询的 record_id（排除已有的）
    needed_ids = []
    for cid in ranked_ids:
        chunk = chunk_map.get(cid)
        if not chunk:
            continue
        if chunk.record_id not in item_map:
            needed_ids.append(chunk.record_id)

    # 批量查询 record（1 次 IN 查询替代 N 次逐条查询）
    allowed_project_ids = get_allowed_project_ids(db, user)
    record_map = {}
    if needed_ids:
        unique_ids = list(set(needed_ids))
        records = db.scalars(
            select(model).where(model.id.in_(unique_ids))
        ).all()
        # Python 层面做权限过滤
        for r in records:
            if _check_record_permission(r, user, allowed_project_ids):
                record_map[r.id] = r

    # 构建最终 item_map
    for cid in ranked_ids:
        chunk = chunk_map.get(cid)
        if not chunk:
            continue
        record = record_map.get(chunk.record_id)
        if not record:
            continue
        fused = fused_scores[cid]
        score = fused + 0.4  # 基础偏移，语义/关键词命中比纯结构化匹配更有价值
        if record.id in item_map:
            item = item_map[record.id]
            item.score = max(item.score, score + 0.3)
            if chunk.source_field not in item.source_fields:
                item.source_fields.append(chunk.source_field)
        else:
            item_map[record.id] = _to_retrieved_item(domain, record, score=score, source_fields=[chunk.source_field])

    preliminary = sorted(item_map.values(), key=lambda item: item.score, reverse=True)[:top_k * 2]

    # ===== Reranker 精排 =====
    if reranker_service.enabled and len(preliminary) > 1:
        passages = []
        for item in preliminary:
            parts = [item.title]
            if item.summary:
                parts.append(item.summary)
            passages.append(" | ".join(parts))

        rerank_results = reranker_service.rerank(query_text, passages, top_k=top_k)
        final_items = []
        for rr in rerank_results:
            item = preliminary[rr["index"]]
            item.score = rr["score"]
            final_items.append(item)
        return final_items

    return preliminary[:top_k]


def get_attachments(db: Session, domain: str, record_id: int, user: User) -> List[dict]:
    stmt = select(Attachment).where(Attachment.domain == domain, Attachment.record_id == record_id)
    stmt = apply_permission_filters(stmt, Attachment, db, user)
    items = db.scalars(stmt).all()
    return [
        {
            "id": item.id,
            "domain": item.domain,
            "record_id": item.record_id,
            "original_name": item.original_name,
            "project_id": item.project_id,
            "access_level": item.access_level,
            "download_url": f"/api/attachments/{item.id}/download",
        }
        for item in items
    ]


def _to_retrieved_item(domain: str, row: Any, score: float, source_fields: List[str]) -> RetrievedItem:
    if domain == "policy":
        return RetrievedItem(
            domain=domain,
            record_id=row.id,
            title=row.title,
            score=score,
            summary=row.summary or row.content[:260],
            publish_date=row.publish_date,
            key_fields={"region": row.region, "scope": row.scope, "source_url": row.source_url},
            source_fields=source_fields,
        )
    if domain == "tender":
        return RetrievedItem(
            domain=domain,
            record_id=row.id,
            title=row.title or row.project_name,
            score=score,
            summary=row.content_summary or row.project_name,
            publish_date=row.publish_date,
            key_fields={
                "project_name": row.project_name,
                "tenderer": row.tenderer,
                "winner": getattr(row, "winner", "") or "",
                "agency": row.agency,
                "stage": row.stage,
                "region": row.region,
                "bid_amount": row.bid_amount,
                "source_url": row.source_url,
            },
            source_fields=source_fields,
        )
    return RetrievedItem(
        domain=domain,
        record_id=row.id,
        title=row.enterprise_name,
        score=score,
        summary=row.business_scope or (row.remark[:180] if row.remark else ""),
        publish_date=None,
        key_fields={"unified_social_code": row.unified_social_code, "region": row.region, "industry": row.industry},
        source_fields=source_fields,
    )
