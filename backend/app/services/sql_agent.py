"""
SQL Agent — 结构化查询意图检测与安全 SQL 生成执行。

当用户问题涉及聚合/排序/统计/计数等结构化查询时，
绕过 RAG 向量检索，直接生成 SQL 查询 MySQL 获取精确结果。
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.entities import EnterpriseRecord, PolicyRecord, TenderRecord, User
from app.services.retrieval import apply_permission_filters, escape_like
from app.services.structured_retrieval import extract_entity, extract_industry


# ── 意图检测关键词 ──────────────────────────────────────────────

AGGREGATE_KEYWORDS = (
    "前几", "排名", "前三", "前十", "前五",
    "最多", "最少", "最大", "最小", "最高", "最低",
    "多少个", "有多少", "几个", "总数", "共计", "合计",
    "统计", "汇总", "计数", "平均",
    "最贵", "最便宜",
    "金额最高", "金额最大", "金额最多",
    "中标金额最高", "中标金额最大",
    "哪些项目", "列出所有",
)

ORDER_AMOUNT_KEYWORDS = ("金额", "中标金额", "预算金额", "成交金额", "招标金额", "bid_amount")
COUNT_KEYWORDS = ("多少", "几个", "数量", "总数", "共计", "合计", "多少个")
TOP_N_PATTERN = re.compile(r"前\s*(\d+|[一二三四五六七八九十]+)")

# 金额筛选关键词
AMOUNT_FILTER_KEYWORDS = ("超过", "以上", "高于", "大于", "不低于", "不低于", "不低于")
AMOUNT_CEIL_KEYWORDS = ("以下", "低于", "小于", "不超过", "不高于", "不超过")
# 排序关键词
SORT_KEYWORDS = ("从高到低", "从低到高", "排列", "排序")
# 中标次数关键词
BID_COUNT_KEYWORDS = ("中标次数", "中标最多", "中标最少", "中标.*最多", "中标.*最少")

# 金额数值提取（支持"500万"、"500万元"、"5000000"）
AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*万?")
DOMAIN_TABLE_MAP = {
    "tender": TenderRecord,
    "enterprise": EnterpriseRecord,
    "policy": PolicyRecord,
}

# 白名单：仅允许通过 getattr 访问的 ORM 字段（防止意外暴露敏感属性）
SAFE_ORDER_FIELDS = {
    "tender": {"bid_amount": TenderRecord.bid_amount, "publish_date": TenderRecord.publish_date},
    "enterprise": {},
    "policy": {"publish_date": PolicyRecord.publish_date},
}

SAFE_FILTER_FIELDS = {
    "tender": {
        "stage": TenderRecord.stage,
        "bid_amount": TenderRecord.bid_amount,
        "winner": TenderRecord.winner,
        "tenderer": TenderRecord.tenderer,
        "agency": TenderRecord.agency,
    },
    "enterprise": {"industry": EnterpriseRecord.industry, "business_scope": EnterpriseRecord.business_scope},
    "policy": {},
}


def _chinese_to_int(s: str) -> int:
    """Convert Chinese number to int."""
    mapping = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    return mapping.get(s, int(s) if s.isdigit() else 0)


def _extract_top_n(question: str) -> Optional[int]:
    """Extract 'top N' from question, e.g. '前三' -> 3."""
    match = TOP_N_PATTERN.search(question)
    if match:
        return _chinese_to_int(match.group(1))
    return None


def _extract_amount(question: str) -> Optional[float]:
    """Extract amount from question, e.g. '500万' -> 5000000, '1000000' -> 1000000."""
    match = AMOUNT_PATTERN.search(question)
    if match:
        value = float(match.group(1))
        # 如果后面有"万"，乘以 10000
        if "万" in question[match.start():match.end() + 1]:
            value *= 10000
        return value
    return None


def _extract_amount_filter(question: str) -> Optional[Tuple[str, float]]:
    """Extract amount filter condition. Returns (operator, amount) or None.
    operator: 'gte', 'gt', 'lte', 'lt'
    """
    if any(kw in question for kw in AMOUNT_FILTER_KEYWORDS):
        amount = _extract_amount(question)
        if amount is not None:
            return ("gte", amount)
    if any(kw in question for kw in AMOUNT_CEIL_KEYWORDS):
        amount = _extract_amount(question)
        if amount is not None:
            return ("lte", amount)
    return None


def _detect_sort_direction(question: str) -> Optional[str]:
    """Detect sort direction from question. Returns 'asc' or 'desc'."""
    if any(kw in question for kw in ("从高到低", "从大到小")):
        return "desc"
    if any(kw in question for kw in ("从低到高", "从小到大")):
        return "asc"
    # 默认降序（"排列"、"排序"后面默认从高到低）
    if any(kw in question for kw in SORT_KEYWORDS):
        return "desc"
    return None


def detect_sql_intent(question: str, domain: str) -> Optional[Dict[str, Any]]:
    """
    Detect if the question requires a structured SQL query.
    Returns intent dict if detected, None otherwise.
    """
    q = (question or "").strip()

    if domain == "tender":
        stage_filter = _extract_stage_filter(q)
        if stage_filter and any(kw in q for kw in ("哪些", "列出", "列举", "哪几个", "哪些项目")):
            entity = extract_entity(q)
            if entity and any(kw in q for kw in ("中标", "成交", "供应商", "中标方", "成交方")):
                return {
                    "type": "list",
                    "domain": "tender",
                    "filter_field": "tenderer",
                    "filter_value": entity,
                    "extra_filters": {"stage": stage_filter},
                    "top_n": 50,
                }
            if entity and any(kw in q for kw in ("采购人", "招标人", "发布", "发起")):
                return {
                    "type": "list",
                    "domain": "tender",
                    "filter_field": "tenderer",
                    "filter_value": entity,
                    "extra_filters": {"stage": stage_filter},
                    "top_n": 50,
                }
        # ── 金额筛选 + 排序（例如"超过500万的项目，按金额排列"）──
        amount_filter = _extract_amount_filter(q)
        if amount_filter:
            op, amount_value = amount_filter
            sort_dir = _detect_sort_direction(q) or "desc"
            top_n = _extract_top_n(q) or 10
            return {
                "type": "filter_by_amount",
                "domain": "tender",
                "filter_op": op,
                "filter_amount": amount_value,
                "order": sort_dir,
                "top_n": top_n,
            }

        # ── 纯排序（例如"按金额从高到低排列"）──
        if (any(kw in q for kw in ORDER_AMOUNT_KEYWORDS) and
            any(kw in q for kw in SORT_KEYWORDS)):
            sort_dir = _detect_sort_direction(q) or "desc"
            top_n = _extract_top_n(q) or 10
            return {
                "type": "top_by_amount",
                "domain": "tender",
                "top_n": top_n,
                "order": sort_dir,
                "field": "bid_amount",
            }

        # ── 中标次数统计 ──
        if any(kw in q for kw in ("中标次数", "中标最多", "中标最少")):
            top_n = _extract_top_n(q) or 3
            return {
                "type": "top_by_bid_count",
                "domain": "tender",
                "top_n": top_n,
            }

        # Amount ranking queries
        if any(kw in q for kw in ("金额", "中标金额", "预算金额")) and any(kw in q for kw in ("排名", "前三", "前几", "最高", "最大", "最多", "最贵", "前十")):
            top_n = _extract_top_n(q) or 3
            return {
                "type": "top_by_amount",
                "domain": "tender",
                "top_n": top_n,
                "order": "desc",
                "field": "bid_amount",
            }

        # Count queries
        if any(kw in q for kw in ("多少", "几个", "数量", "总数")) and any(kw in q for kw in ("项目", "标段", "招标", "中标")):
            entity = extract_entity(q)
            stage_filter = _extract_stage_filter(q)
            if entity:
                if "代理" in q:
                    return {
                        "type": "count",
                        "domain": "tender",
                        "filter_field": "agency",
                        "filter_value": entity,
                        "extra_filters": {"stage": stage_filter} if stage_filter else {},
                    }
                if any(kw in q for kw in ("采购人", "招标人", "发布", "发起")):
                    return {
                        "type": "count",
                        "domain": "tender",
                        "filter_field": "tenderer",
                        "filter_value": entity,
                        "extra_filters": {"stage": stage_filter} if stage_filter else {},
                    }
                if any(kw in q for kw in ("中标", "成交", "供应商", "中标方", "成交方")):
                    return {
                        "type": "count",
                        "domain": "tender",
                        "filter_field": "winner",
                        "filter_value": entity,
                        "extra_filters": {"stage": stage_filter or "中标"},
                    }
            return {
                "type": "count",
                "domain": "tender",
                "filter_field": "stage",
                "filter_value": stage_filter,
            }

        # Stage-specific queries (e.g. "有哪些中标项目")
        stage_filter = _extract_stage_filter(q)
        if stage_filter and any(kw in q for kw in ("有哪些", "哪些", "列出", "列举")):
            return {
                "type": "list_by_stage",
                "domain": "tender",
                "stage": stage_filter,
            }

    if domain == "enterprise":
        industry = extract_industry(q)
        entity = extract_entity(q)
        # Count enterprises
        if any(kw in q for kw in ("多少", "几个", "数量", "总数")) and any(kw in q for kw in ("企业", "公司")):
            if industry:
                return {
                    "type": "count",
                    "domain": "enterprise",
                    "filter_field": "industry",
                    "filter_value": industry,
                }
            return {
                "type": "count",
                "domain": "enterprise",
            }

        # List enterprises by industry or business scope.
        if any(kw in q for kw in ("哪些", "列举", "列出", "几家")) and ("企业" in q or "公司" in q):
            if any(kw in q for kw in ("经营范围", "业务")) and entity:
                return {
                    "type": "list",
                    "domain": "enterprise",
                    "filter_field": "business_scope",
                    "filter_value": entity,
                    "top_n": 50,
                }
            return {
                "type": "list",
                "domain": "enterprise",
                "filter_field": "industry" if industry else "",
                "filter_value": industry,
                "top_n": 50,
            }

    if domain == "policy":
        # Count policies
        if any(kw in q for kw in ("多少", "几个", "数量", "总数")) and any(kw in q for kw in ("政策", "法规", "条例")):
            return {
                "type": "count",
                "domain": "policy",
            }

    return None


def _extract_stage_filter(question: str) -> Optional[str]:
    """Extract tender stage from question."""
    stages = ["中标", "招标", "投标", "成交", "废标", "流标", "中标公告", "招标公告"]
    for stage in stages:
        if stage in question:
            # Normalize
            if stage in ("中标公告",):
                return "中标"
            if stage in ("招标公告",):
                return "招标"
            return stage
    return None


# ── SQL 执行 ─────────────────────────────────────────────────────

@dataclass
class SQLResult:
    success: bool
    data: List[Dict[str, Any]]
    answer_text: str
    sql: str
    citations: List[Dict[str, Any]]


def execute_sql_intent(
    db: Session,
    user: User,
    intent: Dict[str, Any],
    question: str,
) -> Optional[SQLResult]:
    """Execute a detected SQL intent and return structured results."""
    intent_type = intent["type"]
    domain = intent["domain"]
    model = DOMAIN_TABLE_MAP[domain]

    try:
        if intent_type == "top_by_amount":
            return _execute_top_by_amount(db, user, model, intent, question)
        elif intent_type == "filter_by_amount":
            return _execute_filter_by_amount(db, user, model, intent, question)
        elif intent_type == "top_by_bid_count":
            return _execute_top_by_bid_count(db, user, model, intent, question)
        elif intent_type == "count":
            return _execute_count(db, user, model, intent, question, domain)
        elif intent_type == "list_by_stage":
            return _execute_list_by_stage(db, user, model, intent, question)
        elif intent_type == "list":
            return _execute_list(db, user, model, intent, question, domain)
    except Exception as exc:
        print(f"[SQLAgent] execution error: {exc}")
        return None

    return None


def _format_amount(value) -> str:
    if value is None:
        return "未知"
    try:
        v = float(value)
        if v >= 10000:
            if v.is_integer():
                return f"{int(v):,} 元（{int(v / 10000):,} 万元）"
            return f"{v:,.2f} 元（{v / 10000:,.2f} 万元）"
        if v.is_integer():
            return f"{int(v):,} 元"
        return f"{v:,.2f} 元"
    except (TypeError, ValueError):
        return str(value)


def _execute_top_by_amount(
    db: Session, user: User, model, intent: Dict, question: str
) -> SQLResult:
    top_n = intent.get("top_n", 3)
    field = intent.get("field", "bid_amount")

    # 白名单校验：拒绝非法字段名
    allowed = SAFE_ORDER_FIELDS.get(intent["domain"], {})
    order_col = allowed.get(field)
    if order_col is None:
        print(f"[SQLAgent] blocked unsafe order field: {field}")
        return SQLResult(success=False, data=[], answer_text="不支持按该字段排序。", sql="", citations=[])

    stmt = select(model).where(order_col.is_not(None))
    stmt = apply_permission_filters(stmt, model, db, user)
    stmt = stmt.order_by(order_col.desc())
    rows = db.scalars(stmt.limit(top_n)).all()

    if not rows:
        return SQLResult(
            success=True, data=[], answer_text="未找到带有有效金额的记录。", sql="", citations=[]
        )

    data = []
    lines = [f"根据查询结果，金额排名前 {len(rows)} 的项目如下：\n"]
    citations = []

    for i, row in enumerate(rows, 1):
        amount = getattr(row, field, None)
        title = getattr(row, "title", None) or getattr(row, "project_name", None) or f"记录 {row.id}"
        tenderer = getattr(row, "tenderer", "")
        stage = getattr(row, "stage", "")
        publish_date = str(getattr(row, "publish_date", "")) if getattr(row, "publish_date", None) else ""

        item = {
            "rank": i,
            "title": title,
            "amount": amount,
            "amount_text": _format_amount(amount),
            "tenderer": tenderer,
            "stage": stage,
            "publish_date": publish_date,
            "record_id": row.id,
        }
        data.append(item)

        line = f"{i}. 《{title}》— 金额：{_format_amount(amount)}"
        if tenderer:
            line += f"，采购人：{tenderer}"
        if stage:
            line += f"，阶段：{stage}"
        if publish_date:
            line += f"，日期：{publish_date}"
        lines.append(line)

        citations.append({
            "domain": "tender",
            "record_id": row.id,
            "title": title,
            "score": 1.0,
            "source_fields": ["structured", "bid_amount"],
            "key_fields": {
                "project_name": getattr(row, "project_name", ""),
                "tenderer": tenderer,
                "stage": stage,
                "bid_amount": amount,
                "publish_date": publish_date,
            },
            "attachments": [],
        })

    return SQLResult(
        success=True,
        data=data,
        answer_text="\n".join(lines),
        sql=f"SELECT ... FROM {model.__tablename__} ORDER BY {field} DESC LIMIT {top_n}",
        citations=citations,
    )


def _execute_filter_by_amount(
    db: Session, user: User, model, intent: Dict, question: str
) -> SQLResult:
    """金额筛选 + 排序，例如"超过500万的项目按金额排列"."""
    filter_op = intent.get("filter_op", "gte")
    amount_value = intent.get("filter_amount", 0)
    order_dir = intent.get("order", "desc")
    top_n = intent.get("top_n", 10)
    field = "bid_amount"

    # 白名单校验
    allowed = SAFE_ORDER_FIELDS.get(intent["domain"], {})
    order_col = allowed.get(field)
    if order_col is None:
        return SQLResult(success=False, data=[], answer_text="不支持按金额字段筛选。", sql="", citations=[])

    stmt = select(model).where(order_col.is_not(None))
    stmt = apply_permission_filters(stmt, model, db, user)

    # 应用金额过滤
    if filter_op in ("gte", "gt"):
        stmt = stmt.where(order_col >= amount_value if filter_op == "gte" else order_col > amount_value)
    elif filter_op in ("lte", "lt"):
        stmt = stmt.where(order_col <= amount_value if filter_op == "lte" else order_col < amount_value)

    # 排序
    if order_dir == "desc":
        stmt = stmt.order_by(order_col.desc())
    else:
        stmt = stmt.order_by(order_col.asc())

    rows = db.scalars(stmt.limit(top_n)).all()

    if not rows:
        op_text = "≥" if filter_op in ("gte", "gt") else "≤"
        return SQLResult(
            success=True, data=[],
            answer_text=f"未找到中标金额{op_text}{_format_amount(amount_value)}的项目记录。",
            sql="", citations=[],
        )

    data = []
    lines = [f"查询到 {len(rows)} 个中标金额{'≥' if filter_op in ('gte','gt') else '≤'}{_format_amount(amount_value)}的项目：\n"]
    citations = []

    for i, row in enumerate(rows, 1):
        amount = getattr(row, field, None)
        title = getattr(row, "title", None) or getattr(row, "project_name", None) or f"记录 {row.id}"
        tenderer = getattr(row, "tenderer", "")
        stage = getattr(row, "stage", "")
        publish_date = str(getattr(row, "publish_date", "")) if getattr(row, "publish_date", None) else ""

        item = {"rank": i, "title": title, "amount": amount, "amount_text": _format_amount(amount)}
        data.append(item)

        line = f"{i}. 《{title}》— 金额：{_format_amount(amount)}"
        if tenderer:
            line += f"，采购人：{tenderer}"
        if stage:
            line += f"，阶段：{stage}"
        lines.append(line)

        citations.append({
            "domain": "tender", "record_id": row.id, "title": title,
            "score": 1.0, "source_fields": ["structured", "bid_amount"],
            "key_fields": {"project_name": getattr(row, "project_name", ""), "tenderer": tenderer, "stage": stage, "bid_amount": amount, "publish_date": publish_date},
            "attachments": [],
        })

    return SQLResult(
        success=True, data=data, answer_text="\n".join(lines),
        sql=f"SELECT ... FROM {model.__tablename__} WHERE {field} {filter_op} {amount_value} ORDER BY {field} {order_dir.upper()} LIMIT {top_n}",
        citations=citations,
    )


def _execute_top_by_bid_count(
    db: Session, user: User, model, intent: Dict, question: str
) -> SQLResult:
    """按中标次数统计，例如"哪家公司中标次数最多"."""
    top_n = intent.get("top_n", 3)

    # 统计各公司的中标次数（仅统计 stage 包含"中标"的记录）
    stmt = (
        select(
            model.winner,
            func.count().label("bid_count"),
        )
        .where(model.winner.isnot(None), model.winner != "", model.stage.ilike("%中标%", escape="\\"))
    )
    stmt = apply_permission_filters(stmt, model, db, user)
    stmt = stmt.group_by(model.winner).order_by(func.count().desc())
    rows = db.execute(stmt.limit(top_n)).all()

    if not rows:
        return SQLResult(
            success=True, data=[],
            answer_text="未找到中标记录，无法统计中标次数。",
            sql="", citations=[],
        )

    lines = [f"中标次数排名前 {len(rows)} 的中标方/成交供应商：\n"]
    data = []
    for i, (name, count) in enumerate(rows, 1):
        line = f"{i}. {name} — 中标 {count} 次"
        lines.append(line)
        data.append({"rank": i, "name": name, "bid_count": count})

    return SQLResult(
        success=True, data=data, answer_text="\n".join(lines),
        sql=f"SELECT winner, COUNT(*) FROM {model.__tablename__} WHERE stage LIKE '%中标%' GROUP BY winner ORDER BY COUNT(*) DESC LIMIT {top_n}",
        citations=[],
    )


def _execute_count(
    db: Session, user: User, model, intent: Dict, question: str, domain: str
) -> SQLResult:
    stmt = select(func.count()).select_from(model)
    stmt = apply_permission_filters(stmt, model, db, user)

    filter_field = intent.get("filter_field")
    filter_value = intent.get("filter_value")
    if filter_field and filter_value:
        # 白名单校验：拒绝非法字段名
        allowed_filters = SAFE_FILTER_FIELDS.get(domain, {})
        filter_col = allowed_filters.get(filter_field)
        if filter_col is not None:
            stmt = stmt.where(filter_col.ilike(f"%{escape_like(filter_value)}%", escape="\\"))
        else:
            print(f"[SQLAgent] blocked unsafe filter field: {filter_field}")
            return SQLResult(success=False, data=[], answer_text="不支持按该字段筛选。", sql="", citations=[])
    for extra_field, extra_value in (intent.get("extra_filters") or {}).items():
        if not extra_value:
            continue
        allowed_filters = SAFE_FILTER_FIELDS.get(domain, {})
        filter_col = allowed_filters.get(extra_field)
        if filter_col is not None:
            stmt = stmt.where(filter_col.ilike(f"%{escape_like(extra_value)}%", escape="\\"))
        else:
            print(f"[SQLAgent] blocked unsafe filter field: {extra_field}")
            return SQLResult(success=False, data=[], answer_text="不支持按该字段筛选。", sql="", citations=[])

    total = db.scalar(stmt) or 0

    domain_labels = {"tender": "招标项目", "enterprise": "企业", "policy": "政策法规"}
    label = domain_labels.get(domain, "记录")
    filter_desc = f"，筛选条件为「{filter_value}」" if filter_value else ""
    if intent.get("extra_filters"):
        extra_desc = "、".join(str(v) for v in intent["extra_filters"].values() if v)
        if extra_desc:
            filter_desc += f"，附加条件为「{extra_desc}」"

    answer_text = f"根据查询结果，当前可见的{label}总数为 **{total}** 条{filter_desc}。"

    return SQLResult(
        success=True,
        data=[{"total": total}],
        answer_text=answer_text,
        sql=f"SELECT COUNT(*) FROM {model.__tablename__}",
        citations=[],
    )


def _execute_list_by_stage(
    db: Session, user: User, model, intent: Dict, question: str
) -> SQLResult:
    stage = intent.get("stage", "")
    stmt = select(model)
    stmt = apply_permission_filters(stmt, model, db, user)
    if stage:
        stmt = stmt.where(model.stage.ilike(f"%{escape_like(stage)}%", escape="\\"))
    stmt = stmt.order_by(model.publish_date.desc())
    rows = db.scalars(stmt.limit(10)).all()

    if not rows:
        return SQLResult(
            success=True, data=[], answer_text=f"未找到阶段为「{stage}」的项目记录。", sql="", citations=[]
        )

    lines = [f"查询到 {len(rows)} 条「{stage}」阶段的项目记录：\n"]
    citations = []

    for i, row in enumerate(rows, 1):
        title = getattr(row, "title", None) or getattr(row, "project_name", None) or f"记录 {row.id}"
        amount = getattr(row, "bid_amount", None)
        tenderer = getattr(row, "tenderer", "")
        publish_date = str(getattr(row, "publish_date", "")) if getattr(row, "publish_date", None) else ""

        line = f"{i}. 《{title}》"
        if tenderer:
            line += f" — {tenderer}"
        if amount:
            line += f" — {_format_amount(amount)}"
        if publish_date:
            line += f" — {publish_date}"
        lines.append(line)

        citations.append({
            "domain": "tender",
            "record_id": row.id,
            "title": title,
            "score": 1.0,
            "source_fields": ["structured"],
            "key_fields": {
                "project_name": getattr(row, "project_name", ""),
                "tenderer": tenderer,
                "stage": stage,
                "bid_amount": amount,
            },
            "attachments": [],
        })

    return SQLResult(
        success=True,
        data=[],
        answer_text="\n".join(lines),
        sql=f"SELECT ... FROM {model.__tablename__} WHERE stage LIKE '%{stage}%' LIMIT 10",
        citations=citations,
    )


def _execute_list(
    db: Session, user: User, model, intent: Dict, question: str, domain: str
) -> SQLResult:
    stmt = select(model)
    stmt = apply_permission_filters(stmt, model, db, user)
    filter_field = intent.get("filter_field")
    filter_value = intent.get("filter_value")
    if filter_field and filter_value:
        allowed_filters = SAFE_FILTER_FIELDS.get(domain, {})
        filter_col = allowed_filters.get(filter_field)
        if filter_col is not None:
            if domain == "tender" and filter_field == "agency":
                stmt = stmt.where(filter_col == filter_value)
            else:
                stmt = stmt.where(filter_col.ilike(f"%{escape_like(filter_value)}%", escape="\\"))
        else:
            return SQLResult(success=False, data=[], answer_text="不支持按该字段筛选。", sql="", citations=[])
    for extra_field, extra_value in (intent.get("extra_filters") or {}).items():
        if not extra_value:
            continue
        extra_col = SAFE_FILTER_FIELDS.get(domain, {}).get(extra_field)
        if extra_col is not None:
            stmt = stmt.where(extra_col.ilike(f"%{escape_like(extra_value)}%", escape="\\"))
    if domain in ("enterprise", "tender"):
        stmt = stmt.order_by(model.id.asc())
    else:
        stmt = stmt.order_by(model.id.desc())
    rows = db.scalars(stmt.limit(intent.get("top_n", 10))).all()

    if not rows:
        return SQLResult(success=True, data=[], answer_text="未找到相关记录。", sql="", citations=[])

    lines = ["查询结果如下：\n"]
    citations = []

    for i, row in enumerate(rows, 1):
        if domain == "enterprise":
            title = getattr(row, "enterprise_name", "")
            code = getattr(row, "unified_social_code", "")
            industry = getattr(row, "industry", "")
            region = getattr(row, "region", "")
            line = f"{i}. {title}（信用代码：{code}）"
            if industry:
                line += f" — 行业：{industry}"
            if region:
                line += f" — 地区：{region}"
            citations.append({
                "domain": "enterprise",
                "record_id": row.id,
                "title": title,
                "score": 1.0,
                "source_fields": ["structured", filter_field or ""],
                "key_fields": {"unified_social_code": code, "industry": industry, "region": region},
                "attachments": [],
            })
        else:
            title = getattr(row, "title", "") or getattr(row, "project_name", "") or f"记录 {row.id}"
            line = f"{i}. {title}"
            citations.append({
                "domain": domain,
                "record_id": row.id,
                "title": title,
                "score": 1.0,
                "source_fields": ["structured"],
                "key_fields": {},
                "attachments": [],
            })
        lines.append(line)

    return SQLResult(
        success=True,
        data=[],
        answer_text="\n".join(lines),
        sql=f"SELECT ... FROM {model.__tablename__} LIMIT 10",
        citations=citations,
    )
