"""
Evaluate retrieval Recall@K across embedding, hybrid, rerank and final stages.

Usage:
    python backend/scripts/evaluate_retrieval_recall.py --limit 30
    python backend/scripts/evaluate_retrieval_recall.py --domain policy --skip-rerank
"""

import argparse
import json
import random
import socket
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import TextChunk, User
from app.services.bm25_retriever import bm25_index
from app.services.embeddings import embedding_service
from app.services.reranker import reranker_service
from app.services.retrieval import BM25_MAX_CHUNKS, DOMAIN_MODEL_MAP, _check_record_permission, get_allowed_project_ids, high_recall_search
from app.services.vector_store import vector_store


DEFAULT_INPUT = BACKEND_DIR / "tests" / "qa_dataset_recall.json"
DEFAULT_OUTPUT_DIR = BACKEND_DIR / "scripts" / "eval_output"
DEFAULT_TOPS = [1, 3, 5, 10, 20]
STAGES = ["embedding", "hybrid", "rerank", "final"]
VECTOR_CHECK_TIMEOUT_SECONDS = 2.0
_VECTOR_READY: Optional[bool] = None


@dataclass
class StageResult:
    chunk_ids: List[int] = field(default_factory=list)
    record_ids: List[int] = field(default_factory=list)
    answer_only: bool = False
    error: str = ""
    diagnostics: dict = field(default_factory=dict)


class RecallAccumulator:
    def __init__(self, tops: Sequence[int]):
        self.tops = list(tops)
        self.data = defaultdict(self._new_bucket)

    def _new_bucket(self):
        return {
            "n": 0,
            "chunk_hits": Counter(),
            "record_hits": Counter(),
            "answer_only": 0,
            "errors": 0,
        }

    def add(self, domain: str, stage: str, gold_chunk_id: int, gold_record_id, result: StageResult) -> None:
        gold_record_ids = set(gold_record_id if isinstance(gold_record_id, list) else [gold_record_id])
        for key in (f"{domain}:{stage}", f"overall:{stage}"):
            bucket = self.data[key]
            bucket["n"] += 1
            if result.answer_only:
                bucket["answer_only"] += 1
            if result.error:
                bucket["errors"] += 1
            for top_k in self.tops:
                if gold_chunk_id in result.chunk_ids[:top_k]:
                    bucket["chunk_hits"][top_k] += 1
                if gold_record_ids.intersection(result.record_ids[:top_k]):
                    bucket["record_hits"][top_k] += 1

    def report(self) -> dict:
        output = {}
        for key, bucket in sorted(self.data.items()):
            domain, stage = key.split(":", 1)
            output.setdefault(domain, {})[stage] = {
                "n": bucket["n"],
                "chunk_recall": {
                    f"R@{top_k}": _pct(bucket["chunk_hits"][top_k], bucket["n"])
                    for top_k in self.tops
                },
                "record_recall": {
                    f"R@{top_k}": _pct(bucket["record_hits"][top_k], bucket["n"])
                    for top_k in self.tops
                },
                "answer_only": bucket["answer_only"],
                "errors": bucket["errors"],
            }
        return output


def _pct(value: int, total: int) -> float:
    return round(value / total * 100, 2) if total else 0.0


def parse_top_k(raw: str) -> List[int]:
    return sorted({int(item.strip()) for item in raw.split(",") if item.strip()})


def load_items(path: Path, domain: Optional[str], limit: Optional[int], seed: int) -> List[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    filtered = [
        item for item in items
        if item.get("gold_status") == "matched"
        and item.get("gold_chunk_id") is not None
        and item.get("gold_record_id") is not None
        and (domain is None or item.get("domain") == domain)
    ]
    random.Random(seed).shuffle(filtered)
    return filtered[:limit] if limit else filtered


def dataset_summary(path: Path, domain: Optional[str]) -> dict:
    items = json.loads(path.read_text(encoding="utf-8"))
    if domain:
        items = [item for item in items if item.get("domain") == domain]
    status_counts = Counter(item.get("gold_status", "unknown") for item in items)
    reason_counts = Counter(
        item.get("gold_unmatched_reason", "unknown")
        for item in items
        if item.get("gold_status") != "matched"
    )
    total = len(items)
    matched = status_counts.get("matched", 0)
    return {
        "total": total,
        "matched": matched,
        "unmatched": status_counts.get("unmatched", 0),
        "matched_rate": _pct(matched, total),
        "status_counts": dict(status_counts),
        "unmatched_reasons": dict(reason_counts),
    }


def embedding_stage(domain: str, question: str, top_k: int) -> StageResult:
    ensure_vector_available()
    vector = embedding_service.embed_query(question)
    hits = vector_store.search(domain, vector, top_k=top_k)
    return StageResult(
        chunk_ids=[hit["chunk_id"] for hit in hits],
        record_ids=[hit["record_id"] for hit in hits],
    )


def ensure_bm25(db: Session, domain: str) -> None:
    if not bm25_index.available or domain in bm25_index._indices:
        return
    total = db.scalar(select(func.count(TextChunk.id)).where(TextChunk.domain == domain)) or 0
    if total > BM25_MAX_CHUNKS:
        print(f"[BM25] skip building index for {domain}: {total} chunks exceeds {BM25_MAX_CHUNKS}", flush=True)
        return
    chunks = db.scalars(select(TextChunk).where(TextChunk.domain == domain)).all()
    bm25_index.build_index(domain, list(chunks))


def hybrid_stage(db: Session, user: User, domain: str, question: str, top_k: int) -> StageResult:
    ensure_vector_available()
    ensure_bm25(db, domain)
    query_vector = embedding_service.embed_query(question)
    vector_hits = vector_store.search(domain, query_vector, top_k=top_k * 2)
    bm25_hits = bm25_index.search(domain, question, top_k=top_k * 2) if bm25_index.available else []

    score_map: Dict[int, dict] = {}
    for hit in vector_hits:
        score_map.setdefault(hit["chunk_id"], {"vector": 0.0, "bm25": 0.0})["vector"] = float(hit["score"])
    for hit in bm25_hits:
        score_map.setdefault(hit["chunk_id"], {"vector": 0.0, "bm25": 0.0})["bm25"] = float(hit["score"])

    if not score_map:
        return StageResult()

    max_vector = max((v["vector"] for v in score_map.values()), default=1.0) or 1.0
    max_bm25 = max((v["bm25"] for v in score_map.values()), default=1.0) or 1.0
    ranked_chunk_ids = sorted(
        score_map,
        key=lambda cid: 0.7 * (score_map[cid]["vector"] / max_vector) + 0.3 * (score_map[cid]["bm25"] / max_bm25),
        reverse=True,
    )[:top_k]

    chunks = _load_permitted_chunks(db, user, domain, ranked_chunk_ids)
    return StageResult(
        chunk_ids=[chunk.id for chunk in chunks],
        record_ids=[chunk.record_id for chunk in chunks],
    )


def rerank_stage(db: Session, user: User, domain: str, question: str, top_k: int) -> StageResult:
    preliminary = hybrid_stage(db, user, domain, question, top_k * 2)
    if not preliminary.chunk_ids or not reranker_service.enabled:
        return preliminary
    chunks = _load_permitted_chunks(db, user, domain, preliminary.chunk_ids)
    chunk_by_id = {chunk.id: chunk for chunk in chunks}
    ordered_chunks = [chunk_by_id[cid] for cid in preliminary.chunk_ids if cid in chunk_by_id]
    passages = [chunk.content for chunk in ordered_chunks]
    reranked = reranker_service.rerank(question, passages, top_k=top_k)
    selected = [ordered_chunks[item["index"]] for item in reranked]
    return StageResult(
        chunk_ids=[chunk.id for chunk in selected],
        record_ids=[chunk.record_id for chunk in selected],
    )


def final_stage(db: Session, user: User, domain: str, question: str, top_k: int, use_reranker: bool = True) -> StageResult:
    ensure_vector_available()
    result = high_recall_search(
        db=db,
        domain=domain,
        user=user,
        query_text=question,
        top_k=top_k,
        use_reranker=use_reranker,
    )
    diagnostics = result.diagnostics
    record_ids = [item.record_id for item in result.items]
    chunk_ids = []
    for record_id in record_ids:
        chunk_ids.extend(diagnostics.get("chunk_ids_by_record", {}).get(str(record_id), []))
    return StageResult(
        chunk_ids=chunk_ids,
        record_ids=record_ids,
        error="; ".join(diagnostics.get("errors", []))[:200],
        diagnostics=diagnostics,
    )


def ensure_vector_available() -> None:
    global _VECTOR_READY
    if _VECTOR_READY is True:
        return
    if _VECTOR_READY is False:
        raise RuntimeError(f"Milvus unavailable at {settings.milvus_host}:{settings.milvus_port}")

    try:
        with socket.create_connection(
            (settings.milvus_host, int(settings.milvus_port)),
            timeout=VECTOR_CHECK_TIMEOUT_SECONDS,
        ):
            _VECTOR_READY = True
    except OSError as exc:
        _VECTOR_READY = False
        raise RuntimeError(f"Milvus unavailable at {settings.milvus_host}:{settings.milvus_port}: {exc}") from exc


def _load_permitted_chunks(db: Session, user: User, domain: str, chunk_ids: Sequence[int]) -> List[TextChunk]:
    if not chunk_ids:
        return []
    chunks = db.scalars(select(TextChunk).where(TextChunk.id.in_(chunk_ids))).all()
    chunk_map = {chunk.id: chunk for chunk in chunks}
    ordered = [chunk_map[cid] for cid in chunk_ids if cid in chunk_map]
    model = DOMAIN_MODEL_MAP[domain]
    record_ids = {chunk.record_id for chunk in ordered}
    records = db.scalars(select(model).where(model.id.in_(record_ids))).all() if record_ids else []
    allowed_project_ids = get_allowed_project_ids(db, user)
    permitted = {
        record.id for record in records
        if _check_record_permission(record, user, allowed_project_ids)
    }
    return [chunk for chunk in ordered if chunk.record_id in permitted]


def evaluate(
    items: Sequence[Mapping],
    tops: Sequence[int],
    skip_rerank: bool,
    progress_every: int,
    final_only: bool = False,
) -> Tuple[dict, List[dict]]:
    accumulator = RecallAccumulator(tops)
    misses = []
    max_top = max(tops)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        if user is None:
            raise RuntimeError("admin user not found")

        for index, item in enumerate(items, start=1):
            domain = item["domain"]
            question = item["question"]
            gold_chunk_id = int(item["gold_chunk_id"])
            gold_record_id = int(item["gold_record_id"])
            gold_record_ids = [
                int(record_id)
                for record_id in item.get("gold_record_ids", [])
                if record_id is not None
            ] or [gold_record_id]

            stage_results = {}
            stages = ["final"] if final_only else STAGES
            for stage in stages:
                if skip_rerank and stage == "rerank":
                    continue
                try:
                    if stage == "embedding":
                        result = embedding_stage(domain, question, max_top)
                    elif stage == "hybrid":
                        result = hybrid_stage(db, user, domain, question, max_top)
                    elif stage == "rerank":
                        result = rerank_stage(db, user, domain, question, max_top)
                    else:
                        result = final_stage(db, user, domain, question, max_top, use_reranker=not skip_rerank)
                except Exception as exc:
                    result = StageResult(error=str(exc)[:200])
                stage_results[stage] = result
                accumulator.add(domain, stage, gold_chunk_id, gold_record_ids, result)

            final_result = stage_results.get("final", StageResult())
            if not set(gold_record_ids).intersection(final_result.record_ids[:max_top]) and len(misses) < 50:
                misses.append({
                    "question": question,
                    "domain": domain,
                    "difficulty": item.get("difficulty", ""),
                    "question_type": item.get("question_type", ""),
                    "gold_chunk_id": gold_chunk_id,
                    "gold_record_id": gold_record_id,
                    "gold_record_ids": gold_record_ids,
                    "retrieved": {
                        stage: {
                            "chunk_ids": result.chunk_ids[:max_top],
                            "record_ids": result.record_ids[:max_top],
                            "answer_only": result.answer_only,
                            "error": result.error,
                            "sources_by_record": result.diagnostics.get("sources_by_record", {}),
                            "chunk_ids_by_record": result.diagnostics.get("chunk_ids_by_record", {}),
                            "sources": result.diagnostics.get("sources", {}),
                        }
                        for stage, result in stage_results.items()
                    },
                })

            if progress_every and index % progress_every == 0:
                print(f"[Recall] processed {index}/{len(items)}", flush=True)

    return accumulator.report(), misses


def vector_index_summary() -> dict:
    with SessionLocal() as db:
        domains = db.scalars(select(TextChunk.domain).distinct()).all()
        rows = []
        for domain in domains:
            total = db.scalar(select(func.count(TextChunk.id)).where(TextChunk.domain == domain)) or 0
            indexed = db.scalar(
                select(func.count(TextChunk.id)).where(
                    TextChunk.domain == domain,
                    TextChunk.vector_indexed.is_(True),
                )
            ) or 0
            rows.append((domain, total, indexed))
    return {
        domain: {
            "chunks": int(total or 0),
            "vector_indexed": int(indexed or 0),
            "complete": int(total or 0) == int(indexed or 0),
        }
        for domain, total, indexed in rows
    }


def report_has_errors(metrics: dict) -> bool:
    for domain_metrics in metrics.values():
        for stage_metrics in domain_metrics.values():
            if stage_metrics.get("errors", 0):
                return True
    return False


def vector_required_domains(domain: Optional[str]) -> List[str]:
    if domain == "policy":
        return ["policy"]
    if domain in ("enterprise", "tender"):
        return []
    return ["policy"]


def target_summary(
    metrics: dict,
    vector_index: Optional[dict] = None,
    target: float = 92.0,
    domain: Optional[str] = None,
) -> dict:
    final_metrics = metrics.get("overall", {}).get("final", {})
    recall = final_metrics.get("record_recall", {}).get("R@20", 0.0)
    has_errors = report_has_errors(metrics)
    required_domains = vector_required_domains(domain)
    incomplete_vectors = any(
        not (vector_index or {}).get(required_domain, {}).get("complete")
        for required_domain in required_domains
    )
    invalid_reason = ""
    if has_errors:
        invalid_reason = "stage_errors_present"
    elif incomplete_vectors:
        invalid_reason = "vector_index_incomplete"
    return {
        "metric": "overall.final.record_recall.R@20",
        "target": target,
        "achieved": recall,
        "valid": not has_errors and not incomplete_vectors,
        "passed": (not has_errors) and (not incomplete_vectors) and recall >= target,
        "invalid_reason": invalid_reason,
        "vector_required_domains": required_domains,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate retrieval Recall@K.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--domain", choices=["policy", "tender", "enterprise"])
    parser.add_argument("--top-k", default="1,3,5,10,20")
    parser.add_argument("--sample-seed", type=int, default=42)
    parser.add_argument("--skip-rerank", action="store_true")
    parser.add_argument("--final-only", action="store_true", help="Evaluate only the final retrieval stage.")
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--milvus-timeout", type=float, default=2.0)
    args = parser.parse_args()

    global VECTOR_CHECK_TIMEOUT_SECONDS
    VECTOR_CHECK_TIMEOUT_SECONDS = max(args.milvus_timeout, 0.1)

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    tops = parse_top_k(args.top_k)
    items = load_items(input_path, args.domain, args.limit, args.sample_seed)
    metrics, misses = evaluate(items, tops, args.skip_rerank, args.progress_every, args.final_only)

    report = {
        "meta": {
            "eval_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "input": str(input_path),
            "domain": args.domain or "all",
            "limit": args.limit,
            "top_k": tops,
            "skip_rerank": args.skip_rerank,
            "final_only": args.final_only,
            "evaluated_matched_samples": len(items),
        },
        "dataset": dataset_summary(input_path, args.domain),
        "metrics": metrics,
    }
    report["vector_index"] = vector_index_summary()
    report["target"] = target_summary(metrics, report["vector_index"], domain=args.domain)

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "retrieval_recall_report.json"
    misses_path = output_dir / "retrieval_recall_misses.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    misses_path.write_text(json.dumps(misses, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "misses": str(misses_path), **report["dataset"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
