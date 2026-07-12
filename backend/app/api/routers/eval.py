"""
评测专用接口 — 按域分链路获取检索上下文
==========================================
- policy: 走完整混合检索（SQL LIKE + 向量 + BM25 + Reranker）
- enterprise/tender: 走纯 SQL LIKE 结构化检索（跳过向量/BM25/Reranker）

返回格式与 chat/query 兼容，但仅返回检索上下文，不调 LLM 生成回答。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import (
    EnterpriseRecord, PolicyRecord, TenderRecord, User,
)
from app.services.retrieval import (
    DOMAIN_MODEL_MAP, RetrievedItem, _to_retrieved_item,
    apply_permission_filters, escape_like, extract_relevant_snippet, merge_with_semantic_hits,
)
from app.services.sql_agent import detect_sql_intent, execute_sql_intent
from app.services.structured_retrieval import retrieve_structured

router = APIRouter()

DOMAIN_MODEL_MAP = {
    "policy": PolicyRecord,
    "tender": TenderRecord,
    "enterprise": EnterpriseRecord,
}

# 需要走混合检索的域
HYBRID_DOMAINS = {"policy"}


class EvalRetrieveRequest(BaseModel):
    question: str
    domain: str = Field(..., description="policy / tender / enterprise")
    top_k: int = Field(default=5, ge=1, le=20)


class EvalCitationOut(BaseModel):
    domain: str
    record_id: int
    title: str
    score: float
    source_fields: List[str] = Field(default_factory=list)
    key_fields: dict = Field(default_factory=dict)
    summary: str = ""


class EvalRetrieveResponse(BaseModel):
    domain: str
    citations: List[EvalCitationOut] = Field(default_factory=list)
    context_count: int = 0
    answer_text: str = ""
    retrieval_mode: str = ""


@router.post("/retrieve", response_model=EvalRetrieveResponse)
def eval_retrieve(
    payload: EvalRetrieveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """评测专用检索接口：policy 走混合检索，enterprise/tender 走纯 SQL"""
    domain = payload.domain
    q = payload.question
    top_k = payload.top_k

    if domain not in DOMAIN_MODEL_MAP:
        return EvalRetrieveResponse(domain=domain, citations=[], context_count=0, retrieval_mode="invalid")

    if domain in ("tender", "enterprise"):
        sql_intent = detect_sql_intent(q, domain)
        if sql_intent is not None:
            if sql_intent.get("type") == "list":
                sql_intent["top_n"] = top_k
            sql_result = execute_sql_intent(db, current_user, sql_intent, q)
            if sql_result is not None and sql_result.success and (sql_result.data or sql_result.citations):
                citations = [
                    EvalCitationOut(
                        domain=c["domain"],
                        record_id=c["record_id"],
                        title=c["title"],
                        score=c["score"],
                        source_fields=[field for field in c.get("source_fields", []) if field],
                        key_fields=c.get("key_fields", {}),
                        summary=c.get("summary", ""),
                    )
                    for c in sql_result.citations
                ]
                return EvalRetrieveResponse(
                    domain=domain,
                    citations=citations,
                    context_count=len(citations),
                    answer_text=sql_result.answer_text,
                    retrieval_mode="sql",
                )

        structured = retrieve_structured(
            db=db,
            user=current_user,
            domain=domain,
            question=q,
            top_k=top_k,
        )
        if structured.success:
            citations = []
            for citation, context in zip(structured.citations, structured.contexts):
                citations.append(EvalCitationOut(
                    domain=citation.domain,
                    record_id=citation.record_id,
                    title=citation.title,
                    score=citation.score,
                    source_fields=citation.source_fields,
                    key_fields=citation.key_fields,
                    summary=context.get("summary", ""),
                ))
            return EvalRetrieveResponse(
                domain=domain,
                citations=citations,
                context_count=len(citations),
                answer_text=structured.answer_text,
                retrieval_mode="structured",
            )

    model = DOMAIN_MODEL_MAP[domain]
    stmt = select(model)
    stmt = apply_permission_filters(stmt, model, db, current_user)

    # ===== 按域构建 SQL WHERE 条件 =====
    if domain == "policy":
        if q:
            safe_q = escape_like(q)
            stmt = stmt.where(or_(
                model.title.ilike(f"%{safe_q}%", escape="\\"),
                model.summary.ilike(f"%{safe_q}%", escape="\\"),
                model.content.ilike(f"%{safe_q}%", escape="\\"),
            ))
    elif domain == "tender":
        if q:
            safe_q = escape_like(q)
            stmt = stmt.where(or_(
                model.title.ilike(f"%{safe_q}%", escape="\\"),
                model.project_name.ilike(f"%{safe_q}%", escape="\\"),
                model.content_summary.ilike(f"%{safe_q}%", escape="\\"),
            ))
    elif domain == "enterprise":
        if q:
            safe_q = escape_like(q)
            stmt = stmt.where(or_(
                model.enterprise_name.ilike(f"%{safe_q}%", escape="\\"),
                model.unified_social_code.ilike(f"%{safe_q}%", escape="\\"),
                model.business_scope.ilike(f"%{safe_q}%", escape="\\"),
                model.remark.ilike(f"%{safe_q}%", escape="\\"),
            ))

    rows = db.scalars(stmt.limit(top_k)).all()
    items: List[RetrievedItem] = [
        _to_retrieved_item(domain, row, score=1.0, source_fields=["structured"])
        for row in rows
    ]

    # ===== policy 域追加混合检索（向量 + BM25） =====
    if domain in HYBRID_DOMAINS and q:
        items = merge_with_semantic_hits(db, domain, current_user, q, items, top_k)

    items = items[:top_k]

    # ===== 构建响应 =====
    citations = []
    for item in items:
        summary = item.summary or ""
        if domain == "policy":
            row = db.get(PolicyRecord, item.record_id)
            if row is not None:
                summary = extract_relevant_snippet(row.content, q) or summary
        citations.append(EvalCitationOut(
            domain=item.domain,
            record_id=item.record_id,
            title=item.title,
            score=item.score,
            source_fields=item.source_fields,
            key_fields=item.key_fields,
            summary=summary,
        ))

    return EvalRetrieveResponse(
        domain=domain,
        citations=citations,
        context_count=len(citations),
        retrieval_mode="hybrid" if domain in HYBRID_DOMAINS else "structured",
    )
