"""
Build text chunks and Milvus collections from the business tables.

Usage:
    python -m backend.scripts.sync_mysql_index
    python -m backend.scripts.sync_mysql_index --domain tender
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import delete, inspect, text

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.entities import TextChunk
from app.services.embeddings import embedding_service
from app.services.text_splitter import recursive_split_text
from app.services.vector_store import vector_store


@dataclass(frozen=True)
class DomainSyncConfig:
    domain: str
    table: str
    required_columns: Sequence[str]
    content_fields: Sequence[str]
    fallback_fields: Sequence[str]


DOMAIN_CONFIGS: Dict[str, DomainSyncConfig] = {
    "policy": DomainSyncConfig(
        domain="policy",
        table="policy_records",
        required_columns=("id", "title"),
        content_fields=("summary", "content", "scope", "title"),
        fallback_fields=("source_file_name", "source_url", "external_id"),
    ),
    "tender": DomainSyncConfig(
        domain="tender",
        table="tender_records",
        required_columns=("id", "title", "project_name"),
        content_fields=(
            "content_summary",
            "procurement_content",
            "bid_content",
            "winner",
            "procurement_title",
            "title",
            "project_name",
        ),
        fallback_fields=("source_file_name", "source_url", "external_id"),
    ),
    "enterprise": DomainSyncConfig(
        domain="enterprise",
        table="enterprise_records",
        required_columns=("id", "enterprise_name"),
        content_fields=("business_scope", "remark", "industry", "enterprise_name"),
        fallback_fields=("source_file_name", "external_id", "unified_social_code"),
    ),
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _available_columns(table: str) -> List[str]:
    inspector = inspect(engine)
    if not inspector.has_table(table):
        raise RuntimeError(f"business table does not exist: {table}")
    return [column["name"] for column in inspector.get_columns(table)]


def _select_columns(config: DomainSyncConfig, available: Sequence[str]) -> List[str]:
    missing = [column for column in config.required_columns if column not in available]
    if missing:
        raise RuntimeError(f"{config.table} is missing required columns: {', '.join(missing)}")

    wanted = list(config.required_columns) + list(config.content_fields) + list(config.fallback_fields)
    selected = []
    for column in wanted:
        if column in available and column not in selected:
            selected.append(column)
    return selected


def _read_rows(config: DomainSyncConfig, columns: Sequence[str], batch_size: int) -> Iterable[List[Mapping[str, Any]]]:
    column_sql = ", ".join(columns)
    offset = 0
    with SessionLocal() as db:
        while True:
            rows = db.execute(
                text(
                    f"SELECT {column_sql} FROM {config.table} "
                    "ORDER BY id ASC LIMIT :limit OFFSET :offset"
                ),
                {"limit": batch_size, "offset": offset},
            ).mappings().all()
            if not rows:
                break
            yield list(rows)
            offset += batch_size


def _record_fields(config: DomainSyncConfig, row: Mapping[str, Any]) -> List[Tuple[str, str]]:
    fields = []
    for field in config.content_fields:
        value = _safe_text(row.get(field))
        if value:
            fields.append((field, value))
    return fields


def _create_chunks(config: DomainSyncConfig, rows: Sequence[Mapping[str, Any]]) -> List[TextChunk]:
    chunks: List[TextChunk] = []
    for row in rows:
        record_id = int(row["id"])
        external_id = _safe_text(row.get("external_id"))
        for field, value in _record_fields(config, row):
            for order, part in enumerate(recursive_split_text(value)):
                chunks.append(
                    TextChunk(
                        domain=config.domain,
                        record_id=record_id,
                        chunk_key=f"{config.domain}:{record_id}:{field}:{order}",
                        source_field=field,
                        chunk_order=order,
                        content=part,
                        content_preview=part[:120],
                        embedding_model=settings.embedding_model or "hashing",
                        vector_indexed=False,
                        metadata_json=json.dumps(
                            {
                                "domain": config.domain,
                                "table": config.table,
                                "field": field,
                                "external_id": external_id,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
    return chunks


def _embed_and_index(
    domain: str,
    chunks: Sequence[TextChunk],
    embedding_batch_size: int,
    embedding_workers: int,
) -> int:
    if not chunks:
        return 0

    def _log_embedding_progress(done: int, current_total: int) -> None:
        print(
            f"[Sync] {domain}: embedding {done}/{current_total} chunks in current batch",
            flush=True,
        )

    vectors = embedding_service.embed_batch(
        [chunk.content for chunk in chunks],
        batch_size=embedding_batch_size,
        num_workers=embedding_workers,
        progress_callback=_log_embedding_progress,
    )
    embeddings = []
    for chunk, vector in zip(chunks, vectors):
        embeddings.append(
            {
                "chunk_id": chunk.id,
                "record_id": chunk.record_id,
                "source_field": chunk.source_field,
                "metadata": {"domain": domain, "chunk_key": chunk.chunk_key},
                "vector": vector,
            }
        )
        chunk.vector_indexed = True
    vector_store.upsert(domain, embeddings)
    return len(embeddings)


def sync_domain(
    domain: str,
    batch_size: int,
    embedding_batch_size: int,
    embedding_workers: int,
    chunks_only: bool,
) -> Dict[str, int]:
    config = DOMAIN_CONFIGS[domain]
    columns = _select_columns(config, _available_columns(config.table))

    with SessionLocal() as db:
        db.execute(delete(TextChunk).where(TextChunk.domain == domain))
        db.commit()

    vector_store.rebuild(domain, [])

    record_count = 0
    chunk_count = 0
    indexed_count = 0

    for rows in _read_rows(config, columns, batch_size):
        record_count += len(rows)
        chunks = _create_chunks(config, rows)
        if not chunks:
            print(f"[Sync] {domain}: {record_count} records scanned, no chunks in this batch")
            continue

        with SessionLocal() as db:
            db.add_all(chunks)
            db.commit()
            chunk_count += len(chunks)

            if chunks_only:
                print(f"[Sync] {domain}: {record_count} records, {chunk_count} chunks inserted, vectors skipped")
                continue

            indexed_count += _embed_and_index(domain, chunks, embedding_batch_size, embedding_workers)
            db.commit()

        print(f"[Sync] {domain}: {record_count} records, {chunk_count} chunks, {indexed_count} vectors")

    return {"records": record_count, "chunks": chunk_count, "vectors": indexed_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync MySQL business tables into text_chunks and Milvus collections.")
    parser.add_argument("--domain", choices=sorted(DOMAIN_CONFIGS), help="Only sync one domain.")
    parser.add_argument("--batch-size", type=int, default=200, help="Business records per batch.")
    parser.add_argument("--embedding-batch", type=int, default=64, help="Text chunks per embedding batch.")
    parser.add_argument("--embedding-workers", type=int, default=1, help="Embedding worker processes for sentence-transformers.")
    parser.add_argument("--chunks-only", action="store_true", help="Only write text chunks and skip embedding / Milvus writes.")
    args = parser.parse_args()

    vector_store.ensure_collections()
    domains = [args.domain] if args.domain else ["policy", "tender", "enterprise"]
    print(f"[Sync] database_url={settings.database_url}")
    print(f"[Sync] milvus={vector_store.uri}")
    for domain in domains:
        result = sync_domain(
            domain,
            args.batch_size,
            args.embedding_batch,
            args.embedding_workers,
            args.chunks_only,
        )
        print(f"[Sync] {domain}: {result['records']} records, {result['chunks']} chunks, {result['vectors']} vectors")
    print("[Sync] done")


if __name__ == "__main__":
    main()
