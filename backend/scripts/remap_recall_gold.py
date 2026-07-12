"""
Remap QA recall gold labels to the current text_chunks table.

Usage:
    python backend/scripts/remap_recall_gold.py
    python backend/scripts/remap_recall_gold.py --limit 100

The original QA files are left untouched. This script writes a recall-specific
dataset with gold_chunk_id/gold_record_id fields aligned to the current DB.
"""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.entities import EnterpriseRecord, TenderRecord, TextChunk
from app.services.retrieval import escape_like
from app.services.structured_retrieval import detect_structured_intent


DEFAULT_INPUT = BACKEND_DIR / "tests" / "qa_dataset_merged.json"
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "qa_dataset_recall.json"


@dataclass
class GoldCandidate:
    chunk_id: int
    record_id: int
    source_field: str
    score: float
    method: str


STRUCTURED_FIELD_ALIASES = {
    "tender": {
        "project": ("title", "project_name", "project_code"),
        "any": ("title", "project_name", "project_code", "agency", "tenderer", "winner"),
    },
    "enterprise": {
        "enterprise_name": ("enterprise_name", "unified_social_code"),
    },
}
FUZZY_STRUCTURED_FIELDS = {
    "tender": {"project_code", "tenderer", "winner"},
    "enterprise": {"enterprise_name", "unified_social_code", "industry"},
}


def normalize_text(value: str) -> str:
    text = re.sub(r"\s+", "", value or "")
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    text = text.replace("_", "")
    return text.lower()


def char_overlap_score(source_text: str, chunk_text: str) -> float:
    source = normalize_text(source_text)
    chunk = normalize_text(chunk_text)
    if not source or not chunk:
        return 0.0
    if source in chunk:
        return 1.0
    source_chars = set(source)
    chunk_chars = set(chunk)
    return len(source_chars & chunk_chars) / max(len(source_chars), 1)


def source_fragments(source_text: str, max_fragments: int = 3, min_len: int = 16) -> List[str]:
    normalized = re.sub(r"\s+", " ", source_text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= 80:
        return [normalized]
    starts = [0, max((len(normalized) - 80) // 2, 0), max(len(normalized) - 80, 0)]
    fragments = []
    for start in starts:
        fragment = normalized[start:start + 80].strip()
        if len(fragment) >= min_len and fragment not in fragments:
            fragments.append(fragment)
        if len(fragments) >= max_fragments:
            break
    return fragments


def match_gold_chunk(
    db: Session,
    item: Mapping,
    min_score: float = 0.78,
    ambiguity_margin: float = 0.02,
    max_candidates: int = 50,
) -> Optional[GoldCandidate]:
    candidate, _reason = match_gold_chunk_with_reason(
        db,
        item,
        min_score=min_score,
        ambiguity_margin=ambiguity_margin,
        max_candidates=max_candidates,
    )
    return candidate


def match_gold_chunk_with_reason(
    db: Session,
    item: Mapping,
    min_score: float = 0.78,
    ambiguity_margin: float = 0.02,
    max_candidates: int = 50,
    structured_cache: Optional[Dict[Tuple[str, str, str], Sequence[object]]] = None,
    structured_prefetch: Optional[Dict[Tuple[str, str], object]] = None,
) -> Tuple[Optional[GoldCandidate], str]:
    domain = item.get("domain")
    source_text = item.get("source_text") or ""
    source_field = item.get("source_field") or ""
    if not domain:
        return None, "missing_source_text"
    if is_contextual_enterprise_item(item):
        return None, "context_required_no_record_gold"
    original_chunk = match_existing_chunk_id(db, item)
    if original_chunk is not None:
        return original_chunk, "matched"
    if not source_text:
        return match_structured_record_gold(
            db,
            item,
            max_candidates=max_candidates,
            structured_cache=structured_cache,
            structured_prefetch=structured_prefetch,
        )

    candidates = _query_candidates(db, domain, source_field, source_text, max_candidates)
    if not candidates:
        return None, "no_candidate"

    return select_best_candidate(
        item,
        candidates,
        min_score=min_score,
        ambiguity_margin=ambiguity_margin,
    )


def is_contextual_enterprise_item(item: Mapping) -> bool:
    if item.get("domain") != "enterprise":
        return False
    if item.get("source_field") != "business_scope":
        return False
    if not isinstance(item.get("chunk_id"), int):
        return False
    question = item.get("question") or ""
    detected = detect_structured_intent(question, "enterprise")
    if detected.get("intent"):
        return False
    markers = ("该企业", "这家企业", "这家公司", "给定", "经营范围", "主要业务", "是否有资格")
    return any(marker in question for marker in markers)


def match_existing_chunk_id(db: Session, item: Mapping) -> Optional[GoldCandidate]:
    chunk_id = item.get("chunk_id")
    if not isinstance(chunk_id, int):
        return None
    chunk = db.get(TextChunk, chunk_id)
    if chunk is None or chunk.domain != item.get("domain"):
        return None
    return GoldCandidate(
        chunk_id=chunk.id,
        record_id=chunk.record_id,
        source_field=chunk.source_field,
        score=1.0,
        method="existing_chunk_id",
    )


def match_structured_record_gold(
    db: Session,
    item: Mapping,
    max_candidates: int = 50,
    structured_cache: Optional[Dict[Tuple[str, str, str], Sequence[object]]] = None,
    structured_prefetch: Optional[Dict[Tuple[str, str], object]] = None,
) -> Tuple[Optional[GoldCandidate], str]:
    domain = item.get("domain") or ""
    if domain not in ("enterprise", "tender"):
        return None, "missing_source_text"
    chunk_id = str(item.get("chunk_id") or "")
    if any(marker in chunk_id for marker in ("count", "stats", "ranking")):
        return None, "aggregate_no_record_gold"
    if domain == "tender" and chunk_id in ("tender_project_list",):
        return None, "aggregate_no_record_gold"
    item_field = item.get("source_field") or ""
    if domain == "tender" and item_field in ("title", "project", "project_name"):
        rows = _query_tender_answer_title(db, item, max_candidates, structured_prefetch)
        if not rows:
            return None, "no_structured_candidate"
        row = rows[0]
        return (
            GoldCandidate(
                chunk_id=0,
                record_id=int(row.id),
                source_field="title",
                score=1.0,
                method="structured_answer_title_match",
            ),
            "matched",
        )

    question = item.get("question") or ""
    detected = detect_structured_intent(question, domain)
    entity = detected.get("entity") or ""
    detected_field = detected.get("field") or ""
    source_field = item_field if item_field and detected_field in ("", "any") else (detected_field or item_field)
    if not entity or not source_field:
        return None, "missing_structured_entity"

    if domain == "tender":
        rows = _query_tender_answer_title(db, item, max_candidates, structured_prefetch)
        if rows:
            row = rows[0]
            return (
                GoldCandidate(
                    chunk_id=0,
                    record_id=int(row.id),
                    source_field="title",
                    score=1.0,
                    method="structured_answer_title_match",
                ),
                "matched",
            )
        if source_field in ("title", "project", "project_name"):
            return None, "no_structured_candidate"
    if domain == "enterprise":
        rows = _query_enterprise_answer_name(db, item, max_candidates, structured_prefetch)
        if rows:
            row = rows[0]
            return (
                GoldCandidate(
                    chunk_id=0,
                    record_id=int(row.id),
                    source_field="enterprise_name",
                    score=1.0,
                    method="structured_answer_name_match",
                ),
                "matched",
            )

    cache_key = (domain, source_field, normalize_text(entity))
    if structured_cache is not None and cache_key in structured_cache:
        rows = structured_cache[cache_key]
    else:
        rows = _query_structured_records(db, domain, source_field, entity, max_candidates)
        if structured_cache is not None:
            structured_cache[cache_key] = rows
    if not rows:
        return None, "no_structured_candidate"

    row = rows[0]
    return (
        GoldCandidate(
            chunk_id=0,
            record_id=int(row.id),
            source_field=source_field,
            score=1.0,
            method="structured_record_match",
        ),
        "matched",
    )


def _query_structured_records(
    db: Session,
    domain: str,
    source_field: str,
    entity: str,
    max_candidates: int,
) -> Sequence[object]:
    model = TenderRecord if domain == "tender" else EnterpriseRecord
    fields = STRUCTURED_FIELD_ALIASES.get(domain, {}).get(source_field, (source_field,))
    safe = escape_like(entity)

    conditions = []
    for field in fields:
        column = getattr(model, field, None)
        if column is not None:
            conditions.append(column == entity)
    if not conditions:
        return []

    stmt = select(model).where(or_(*conditions)).limit(1)
    rows = list(db.scalars(stmt).all())
    if rows:
        return rows

    fuzzy_conditions = []
    for field in fields:
        if field not in FUZZY_STRUCTURED_FIELDS.get(domain, set()):
            continue
        column = getattr(model, field, None)
        if column is not None:
            fuzzy_conditions.append(column.ilike(f"%{safe}%", escape="\\"))
    if not fuzzy_conditions:
        return []
    stmt = select(model).where(or_(*fuzzy_conditions)).limit(1)
    return list(db.scalars(stmt).all())


def _query_tender_answer_title(
    db: Session,
    item: Mapping,
    max_candidates: int,
    structured_prefetch: Optional[Dict[Tuple[str, str], object]] = None,
) -> Sequence[TenderRecord]:
    answer = item.get("answer") or ""
    question = item.get("question") or ""
    titles = re.findall(r"《([^》]{2,255})》", answer)
    titles.extend(re.findall(r"[\"“”]([^\"“”]{2,255})[\"“”]", question))
    for title in titles:
        if structured_prefetch is not None:
            row = structured_prefetch.get(("tender_title", title)) or structured_prefetch.get(("tender_project_name", title))
            if row is not None:
                return [row]
            continue
        for column in (TenderRecord.title, TenderRecord.project_name):
            stmt = (
                select(TenderRecord)
                .where(column == title)
                .limit(1)
            )
            rows = list(db.scalars(stmt).all())
            if rows:
                return rows
    return []


def tender_answer_record_ids(item: Mapping, structured_prefetch: Dict[Tuple[str, str], object]) -> List[int]:
    answer = item.get("answer") or ""
    question = item.get("question") or ""
    titles = re.findall(r"《([^》]{2,255})》", answer)
    titles.extend(re.findall(r"[\"“”]([^\"“”]{2,255})[\"“”]", question))
    record_ids = []
    for title in titles:
        row = structured_prefetch.get(("tender_title", title)) or structured_prefetch.get(("tender_project_name", title))
        if row is not None and int(row.id) not in record_ids:
            record_ids.append(int(row.id))
    return record_ids


def _query_enterprise_answer_name(
    db: Session,
    item: Mapping,
    max_candidates: int,
    structured_prefetch: Optional[Dict[Tuple[str, str], object]] = None,
) -> Sequence[EnterpriseRecord]:
    answer = item.get("answer") or ""
    tail = re.split(r"[:锛歔：]", answer, maxsplit=1)
    candidates = tail[1] if len(tail) > 1 else answer
    names = [
        value.strip(" 。.；;，,、")
        for value in re.split(r"[；;，,、\n]", candidates)
        if 2 <= len(value.strip()) <= 255
    ]
    for name in names:
        if structured_prefetch is not None:
            row = structured_prefetch.get(("enterprise_name", name))
            if row is not None:
                return [row]
            continue
        stmt = (
            select(EnterpriseRecord)
            .where(EnterpriseRecord.enterprise_name == name)
            .order_by(EnterpriseRecord.id.desc())
            .limit(max_candidates)
        )
        rows = list(db.scalars(stmt).all())
        if rows:
            return rows
    return []


def enterprise_answer_record_ids(item: Mapping, structured_prefetch: Dict[Tuple[str, str], object]) -> List[int]:
    names = _extract_enterprise_answer_names(item)
    record_ids = []
    for name in names:
        row = structured_prefetch.get(("enterprise_name", name))
        if row is not None and int(row.id) not in record_ids:
            record_ids.append(int(row.id))
    return record_ids


def _extract_enterprise_answer_names(item: Mapping) -> List[str]:
    answer = item.get("answer") or ""
    tail = re.split(r"[:锛歔：]", answer, maxsplit=1)
    candidates = tail[1] if len(tail) > 1 else answer
    return [
        value.strip(" 。.；;，,、")
        for value in re.split(r"[；;，,、\n]", candidates)
        if 2 <= len(value.strip()) <= 255
    ]


def build_structured_prefetch(db: Session, items: Sequence[Mapping], batch_size: int = 500) -> Dict[Tuple[str, str], object]:
    tender_titles = set()
    enterprise_names = set()
    for item in items:
        domain = item.get("domain")
        if domain == "tender":
            tender_titles.update(re.findall(r"《([^》]{2,255})》", item.get("answer") or ""))
            tender_titles.update(re.findall(r"[\"“”]([^\"“”]{2,255})[\"“”]", item.get("question") or ""))
        elif domain == "enterprise":
            enterprise_names.update(_extract_enterprise_answer_names(item))

    output: Dict[Tuple[str, str], object] = {}
    if tender_titles:
        needed = set(tender_titles)
        found = set()
        rows = db.execute(select(TenderRecord.id, TenderRecord.title, TenderRecord.project_name)).all()
        for record_id, title, project_name in rows:
            if title in needed:
                output.setdefault(("tender_title", title), SimpleNamespace(id=record_id))
                found.add(title)
            if project_name in needed:
                output.setdefault(("tender_project_name", project_name), SimpleNamespace(id=record_id))
                found.add(project_name)
            if len(found) >= len(needed):
                break
    for batch in _batched(sorted(enterprise_names), batch_size):
        rows = db.execute(select(EnterpriseRecord.id, EnterpriseRecord.enterprise_name).where(EnterpriseRecord.enterprise_name.in_(batch))).all()
        for record_id, enterprise_name in rows:
            output.setdefault(("enterprise_name", enterprise_name), SimpleNamespace(id=record_id))
    return output


def _batched(values: Sequence[str], batch_size: int) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), batch_size):
        yield values[index:index + batch_size]


def select_best_candidate(
    item: Mapping,
    candidates: Sequence[TextChunk],
    min_score: float = 0.78,
    ambiguity_margin: float = 0.02,
) -> Tuple[Optional[GoldCandidate], str]:
    source_text = item.get("source_text") or ""
    scored = [
        GoldCandidate(
            chunk_id=chunk.id,
            record_id=chunk.record_id,
            source_field=chunk.source_field,
            score=char_overlap_score(source_text, chunk.content or ""),
            method="source_text_contains",
        )
        for chunk in candidates
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    if not scored:
        return None, "no_candidate"
    best = scored[0]
    if best.score < min_score:
        return None, "low_score"
    if len(scored) > 1 and best.score < 1.0 and (best.score - scored[1].score) < ambiguity_margin:
        return None, "ambiguous"
    return best, "matched"


def _query_candidates(
    db: Session,
    domain: str,
    source_field: str,
    source_text: str,
    max_candidates: int,
) -> Sequence[TextChunk]:
    fragments = source_fragments(source_text)
    if not fragments:
        return []

    seen = {}
    for fragment in fragments:
        stmt = select(TextChunk).where(
            TextChunk.domain == domain,
            TextChunk.content.ilike(f"%{escape_like(fragment)}%", escape="\\"),
        )
        if source_field:
            stmt = stmt.where(TextChunk.source_field == source_field)
        for chunk in db.scalars(stmt.limit(max_candidates)).all():
            seen[chunk.id] = chunk
        if seen:
            break

    if not seen and source_field:
        # Retry without source_field because old datasets may have field names
        # that differ from the current chunking config.
        for fragment in fragments:
            stmt = select(TextChunk).where(
                TextChunk.domain == domain,
                TextChunk.content.ilike(f"%{escape_like(fragment)}%", escape="\\"),
            )
            for chunk in db.scalars(stmt.limit(max_candidates)).all():
                seen[chunk.id] = chunk
            if seen:
                break
    return list(seen.values())


def remap_items(db: Session, items: Iterable[Mapping], limit: Optional[int] = None) -> List[dict]:
    output = []
    source_items = list(items)
    if limit is not None:
        source_items = source_items[:limit]
    structured_cache: Dict[Tuple[str, str, str], Sequence[object]] = {}
    structured_prefetch = build_structured_prefetch(db, source_items)
    for item in source_items:
        mapped = dict(item)
        mapped.setdefault("original_chunk_id", item.get("chunk_id"))
        candidate, reason = match_gold_chunk_with_reason(
            db,
            item,
            structured_cache=structured_cache,
            structured_prefetch=structured_prefetch,
        )
        if candidate is None:
            mapped.update({
                "gold_chunk_id": None,
                "gold_record_id": None,
                "gold_match_method": "",
                "gold_match_score": 0.0,
                "gold_status": "unmatched",
                "gold_unmatched_reason": reason,
            })
        else:
            mapped.update({
                "gold_chunk_id": candidate.chunk_id,
                "gold_record_id": candidate.record_id,
                "gold_match_method": candidate.method,
                "gold_match_score": round(candidate.score, 4),
                "gold_status": "matched",
                "gold_unmatched_reason": "",
            })
            if candidate.method == "structured_answer_title_match":
                mapped["gold_record_ids"] = tender_answer_record_ids(item, structured_prefetch)
            elif candidate.method == "structured_answer_name_match":
                mapped["gold_record_ids"] = enterprise_answer_record_ids(item, structured_prefetch)
        output.append(mapped)
    return output


def summarize(items: Sequence[Mapping]) -> dict:
    status_counts = Counter(item.get("gold_status", "unknown") for item in items)
    reason_counts = Counter(
        item.get("gold_unmatched_reason", "unknown")
        for item in items
        if item.get("gold_status") != "matched"
    )
    domain_counts = Counter(item.get("domain", "unknown") for item in items)
    matched = status_counts.get("matched", 0)
    total = len(items)
    return {
        "total": total,
        "matched": matched,
        "unmatched": status_counts.get("unmatched", 0),
        "matched_rate": round(matched / total * 100, 2) if total else 0.0,
        "status_counts": dict(status_counts),
        "unmatched_reasons": dict(reason_counts),
        "domain_counts": dict(domain_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Remap QA gold chunks for retrieval recall evaluation.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    items = json.loads(input_path.read_text(encoding="utf-8"))

    with SessionLocal() as db:
        remapped = remap_items(db, items, limit=args.limit)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(remapped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), **summarize(remapped)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
