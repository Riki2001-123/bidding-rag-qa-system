import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import EnterpriseRecord, TenderRecord, User
from app.schemas.common import CitationOut
from app.services.retrieval import apply_permission_filters, escape_like


ENTITY_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "项目管理有限公司",
    "咨询有限公司",
    "有限公司",
    "研究院",
    "管理局",
    "委员会",
    "集团",
    "医院",
    "学校",
    "大学",
    "学院",
    "中心",
    "公司",
)

AGENCY_KEYWORDS = ("代理", "代理过", "招标代理", "代理机构")
WINNER_KEYWORDS = ("中标", "成交", "供应商", "中标方", "成交方")
TENDERER_KEYWORDS = ("采购人", "招标人", "发布", "发起", "采购单位", "招标单位")
PROJECT_DETAIL_KEYWORDS = ("项目", "标段", "公告", "详情", "金额", "采购人", "招标人")
ENTERPRISE_DETAIL_KEYWORDS = ("介绍", "基本信息", "这家企业", "这家公司", "企业信息")
ENTERPRISE_SCOPE_KEYWORDS = ("经营范围", "业务领域", "擅长", "能否", "是否适合", "资质", "能力")
INDUSTRY_LIST_KEYWORDS = ("行业",)
GENERIC_ENTERPRISE_REFERENCES = ("这家公司", "这家企业", "该公司", "该企业", "给定的信息", "给定信息")


@dataclass
class StructuredRetrievalResult:
    success: bool
    domain: str
    intent: str = ""
    entities: Dict[str, Any] = field(default_factory=dict)
    answer_text: str = ""
    citations: List[CitationOut] = field(default_factory=list)
    contexts: List[dict] = field(default_factory=list)


def retrieve_structured(
    db: Session,
    user: User,
    domain: str,
    question: str,
    top_k: int = 5,
) -> StructuredRetrievalResult:
    if domain == "tender":
        return _retrieve_tender(db, user, question, top_k)
    if domain == "enterprise":
        return _retrieve_enterprise(db, user, question, top_k)
    return StructuredRetrievalResult(success=False, domain=domain)


def detect_structured_intent(question: str, domain: str) -> Dict[str, Any]:
    q = (question or "").strip()
    entity = extract_entity(q)
    if domain == "tender":
        if entity and _has_quoted_entity(q):
            return {"intent": "tender_detail", "entity": entity, "field": "project"}
        if any(keyword in q for keyword in AGENCY_KEYWORDS):
            return {"intent": "tender_by_agency", "entity": entity, "field": "agency"}
        if any(keyword in q for keyword in TENDERER_KEYWORDS):
            return {"intent": "tender_by_tenderer", "entity": entity, "field": "tenderer"}
        if any(keyword in q for keyword in WINNER_KEYWORDS):
            return {"intent": "tender_by_winner", "entity": entity, "field": "winner"}
        if entity and any(keyword in q for keyword in PROJECT_DETAIL_KEYWORDS):
            return {"intent": "tender_detail", "entity": entity, "field": "project"}
        if entity:
            return {"intent": "tender_entity_search", "entity": entity, "field": "any"}
    if domain == "enterprise":
        if _is_generic_enterprise_question(q):
            return {"intent": "", "entity": "", "field": ""}
        if any(keyword in q for keyword in INDUSTRY_LIST_KEYWORDS) and ("企业" in q or "公司" in q):
            return {"intent": "enterprise_by_industry", "entity": extract_industry(q), "field": "industry"}
        if any(keyword in q for keyword in ENTERPRISE_SCOPE_KEYWORDS):
            return {"intent": "enterprise_scope_match", "entity": entity, "field": "business_scope"}
        if entity and any(keyword in q for keyword in ENTERPRISE_DETAIL_KEYWORDS):
            return {"intent": "enterprise_detail", "entity": entity, "field": "enterprise_name"}
        if entity:
            return {"intent": "enterprise_detail", "entity": entity, "field": "enterprise_name"}
    return {"intent": "", "entity": entity, "field": ""}


def _has_quoted_entity(question: str) -> bool:
    return bool(re.search(r"[\"“”鈥溿€?]([^\"“”鈥濄€?]{2,255})[\"“”鈥濄€?]", question or ""))


def _is_generic_enterprise_question(question: str) -> bool:
    if not any(marker in question for marker in GENERIC_ENTERPRISE_REFERENCES):
        return False
    if re.search(r"[\"“《']([^\"”》']{2,80})[\"”》']", question):
        return False
    stripped = question
    for marker in GENERIC_ENTERPRISE_REFERENCES:
        stripped = stripped.replace(marker, "")
    suffix_pattern = "|".join(re.escape(suffix) for suffix in ENTITY_SUFFIXES)
    return not re.search(rf"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{{2,90}}(?:{suffix_pattern})", stripped)


def extract_industry(question: str) -> str:
    text = (question or "").strip()
    patterns = (
        r"列举几家(.+?)行业的(?:企业|公司)",
        r"收录了多少家(.+?)行业的(?:企业|公司)",
        r"有多少家(.+?)行业的(?:企业|公司)",
        r"(.+?)行业",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = _clean_entity(match.group(1))
            return value
    return ""


def extract_entity(question: str) -> str:
    q = (question or "").strip()
    if not q:
        return ""

    quoted = re.findall(r"[\"“《']([^\"”》']{2,80})[\"”》']", q)
    for value in quoted:
        cleaned = _clean_entity(value)
        if cleaned:
            return cleaned

    suffix_pattern = "|".join(re.escape(suffix) for suffix in ENTITY_SUFFIXES)
    matches = re.findall(rf"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{{2,90}}(?:{suffix_pattern})", q)
    if matches:
        return _clean_entity(max(matches, key=len))

    return _clean_entity(q)


def _clean_entity(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^(请|帮我|查询|查一下|介绍一下|从给定的信息来看|目前系统中收录了多少家)", "", text)
    splitters = (
        "代理过", "代理", "中标了", "中标", "成交了", "成交", "的基本信息", "这家", "主要业务",
        "经营范围", "业务领域", "擅长", "有哪些", "是什么", "分别", "多少", "？", "?", "，", ",",
    )
    for splitter in splitters:
        if splitter in text:
            text = text.split(splitter, 1)[0]
    text = text.strip(" ：:。、“”\"'《》()（）")
    if len(text) < 2:
        return ""
    return text[-80:]


def _retrieve_tender(db: Session, user: User, question: str, top_k: int) -> StructuredRetrievalResult:
    detected = detect_structured_intent(question, "tender")
    entity = detected.get("entity") or ""
    intent = detected.get("intent") or ""
    if not entity or not intent:
        return StructuredRetrievalResult(success=False, domain="tender", intent=intent, entities={"entity": entity})

    stmt = select(TenderRecord)
    stmt = apply_permission_filters(stmt, TenderRecord, db, user)
    safe = escape_like(entity)
    source_fields: List[str]

    if intent == "tender_by_agency":
        stmt = stmt.where(TenderRecord.agency == entity)
        source_fields = ["structured", "agency"]
    elif intent == "tender_by_winner":
        stmt = stmt.where(or_(
            TenderRecord.winner.ilike(f"%{safe}%", escape="\\"),
            TenderRecord.tenderer.ilike(f"%{safe}%", escape="\\"),
        ))
        source_fields = ["structured", "winner", "tenderer"]
    elif intent == "tender_by_tenderer":
        stmt = stmt.where(TenderRecord.tenderer.ilike(f"%{safe}%", escape="\\"))
        source_fields = ["structured", "tenderer"]
    elif intent == "tender_detail":
        stmt = stmt.where(or_(
            TenderRecord.title.ilike(f"%{safe}%", escape="\\"),
            TenderRecord.project_name.ilike(f"%{safe}%", escape="\\"),
            TenderRecord.project_code.ilike(f"%{safe}%", escape="\\"),
        ))
        source_fields = ["structured", "title", "project_name"]
    else:
        stmt = stmt.where(or_(
            TenderRecord.title.ilike(f"%{safe}%", escape="\\"),
            TenderRecord.project_name.ilike(f"%{safe}%", escape="\\"),
            TenderRecord.tenderer.ilike(f"%{safe}%", escape="\\"),
            TenderRecord.winner.ilike(f"%{safe}%", escape="\\"),
            TenderRecord.agency.ilike(f"%{safe}%", escape="\\"),
        ))
        source_fields = ["structured"]

    stmt = stmt.order_by(TenderRecord.id.asc())
    rows = db.scalars(stmt.limit(max(top_k, 1))).all()
    if not rows:
        return StructuredRetrievalResult(success=False, domain="tender", intent=intent, entities={"entity": entity})
    return _build_tender_result(rows, intent, entity, source_fields)


def _retrieve_enterprise(db: Session, user: User, question: str, top_k: int) -> StructuredRetrievalResult:
    detected = detect_structured_intent(question, "enterprise")
    entity = detected.get("entity") or ""
    intent = detected.get("intent") or ""
    if not entity or not intent:
        return StructuredRetrievalResult(success=False, domain="enterprise", intent=intent, entities={"entity": entity})

    safe = escape_like(entity)
    stmt = select(EnterpriseRecord)
    stmt = apply_permission_filters(stmt, EnterpriseRecord, db, user)
    if intent == "enterprise_scope_match":
        stmt = stmt.where(or_(
            EnterpriseRecord.enterprise_name.ilike(f"%{safe}%", escape="\\"),
            EnterpriseRecord.business_scope.ilike(f"%{safe}%", escape="\\"),
            EnterpriseRecord.industry.ilike(f"%{safe}%", escape="\\"),
        ))
        source_fields = ["structured", "enterprise_name", "business_scope"]
    elif intent == "enterprise_by_industry":
        stmt = stmt.where(EnterpriseRecord.industry.ilike(f"%{safe}%", escape="\\"))
        source_fields = ["structured", "industry"]
    else:
        stmt = stmt.where(or_(
            EnterpriseRecord.enterprise_name.ilike(f"%{safe}%", escape="\\"),
            EnterpriseRecord.unified_social_code.ilike(f"%{safe}%", escape="\\"),
        ))
        source_fields = ["structured", "enterprise_name"]

    stmt = stmt.order_by(EnterpriseRecord.id.asc())
    rows = db.scalars(stmt.limit(max(top_k, 1))).all()
    if not rows:
        return StructuredRetrievalResult(success=False, domain="enterprise", intent=intent, entities={"entity": entity})
    return _build_enterprise_result(rows, intent, entity, source_fields)


def _build_tender_result(rows: List[TenderRecord], intent: str, entity: str, source_fields: List[str]) -> StructuredRetrievalResult:
    citations: List[CitationOut] = []
    contexts: List[dict] = []
    lines = [f"直接结论：根据结构化招投标数据，检索到 {len(rows)} 条与“{entity}”相关的项目记录。"]
    for index, row in enumerate(rows, start=1):
        title = row.title or row.project_name or f"招投标记录 {row.id}"
        key_fields = {
            "project_name": row.project_name,
            "tenderer": row.tenderer,
            "winner": row.winner or "当前结构化数据未收录中标方",
            "agency": row.agency,
            "stage": row.stage,
            "region": row.region,
            "bid_amount": row.bid_amount,
            "publish_date": str(row.publish_date) if row.publish_date else "",
            "source_url": row.source_url,
        }
        summary = _tender_summary(row)
        lines.append(f"{index}. {summary}")
        citation = CitationOut(
            domain="tender",
            record_id=row.id,
            title=title,
            score=1.0,
            source_fields=source_fields,
            key_fields=key_fields,
        )
        citations.append(citation)
        contexts.append({
            "domain": "tender",
            "title": title,
            "summary": summary,
            "key_fields": key_fields,
            "attachments": [],
        })
    lines.append("依据说明：以上结果来自 tender_records 结构化字段匹配，不依赖长文本向量召回。")
    return StructuredRetrievalResult(
        success=True,
        domain="tender",
        intent=intent,
        entities={"entity": entity},
        answer_text="\n".join(lines),
        citations=citations,
        contexts=contexts,
    )


def _build_enterprise_result(rows: List[EnterpriseRecord], intent: str, entity: str, source_fields: List[str]) -> StructuredRetrievalResult:
    citations: List[CitationOut] = []
    contexts: List[dict] = []
    lines = [f"直接结论：根据结构化企业数据，检索到 {len(rows)} 条与“{entity}”相关的企业记录。"]
    for index, row in enumerate(rows, start=1):
        title = row.enterprise_name or f"企业记录 {row.id}"
        key_fields = {
            "enterprise_name": row.enterprise_name,
            "unified_social_code": row.unified_social_code,
            "region": row.region,
            "industry": row.industry,
            "business_scope": row.business_scope,
        }
        summary = _enterprise_summary(row)
        lines.append(f"{index}. {summary}")
        citation = CitationOut(
            domain="enterprise",
            record_id=row.id,
            title=title,
            score=1.0,
            source_fields=source_fields,
            key_fields=key_fields,
        )
        citations.append(citation)
        contexts.append({
            "domain": "enterprise",
            "title": title,
            "summary": summary,
            "key_fields": key_fields,
            "attachments": [],
        })
    lines.append("依据说明：以上结果来自 enterprise_records 结构化字段匹配，不依赖长文本向量召回。")
    return StructuredRetrievalResult(
        success=True,
        domain="enterprise",
        intent=intent,
        entities={"entity": entity},
        answer_text="\n".join(lines),
        citations=citations,
        contexts=contexts,
    )


def _tender_summary(row: TenderRecord) -> str:
    winner = row.winner or "当前结构化数据未收录中标方"
    parts = [
        f"项目名称：{row.project_name or row.title or '未填写'}",
        f"采购人/招标人：{row.tenderer or '未填写'}",
        f"中标方/成交供应商：{winner}",
        f"代理机构：{row.agency or '未填写'}",
        f"阶段：{row.stage or '未填写'}",
        f"地区：{row.region or '未填写'}",
        f"金额：{_format_amount(row.bid_amount)}",
        f"发布日期：{row.publish_date or '未填写'}",
    ]
    return "；".join(parts)


def _enterprise_summary(row: EnterpriseRecord) -> str:
    scope = row.business_scope or row.remark or "未填写"
    if len(scope) > 180:
        scope = scope[:180] + "..."
    parts = [
        f"企业名称：{row.enterprise_name or '未填写'}",
        f"统一社会信用代码：{row.unified_social_code or '未填写'}",
        f"地区：{row.region or '未填写'}",
        f"行业：{row.industry or '未填写'}",
        f"经营范围：{scope}",
    ]
    return "；".join(parts)


def _format_amount(value: Optional[float]) -> str:
    if value is None:
        return "未填写"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    if amount.is_integer():
        return f"{int(amount):,} 元"
    return f"{amount:,.2f} 元"
