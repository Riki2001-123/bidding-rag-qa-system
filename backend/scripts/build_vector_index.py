"""
Optimized data import script that chunks business records, generates embeddings,
and writes vectors to Milvus.

Usage:
    cd D:\\python\\PythonProject\\RAG+LLMProject\\backend
    python -m scripts.build_vector_index
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import EnterpriseRecord, PolicyRecord, TenderRecord, TextChunk
from app.services.embeddings import embedding_service
from app.services.vector_store import vector_store


DOMAIN_CONFIG = {
    "policy": {
        "model": PolicyRecord,
        "text_fields": ["title", "summary", "content"],
        "id_field": "external_id",
    },
    "tender": {
        "model": TenderRecord,
        "text_fields": ["title", "project_name", "content_summary", "tenderer", "agency"],
        "id_field": "external_id",
    },
    "enterprise": {
        "model": EnterpriseRecord,
        "text_fields": ["enterprise_name", "business_scope", "remark", "industry", "region"],
        "id_field": "external_id",
    },
}


def chunk_text(text: str, max_length: int = 500, overlap: int = 50) -> List[str]:
    if not text or len(text) <= max_length:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_length
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def build_record_chunks(domain: str, record) -> List[dict]:
    config = DOMAIN_CONFIG[domain]
    chunks = []
    for field_name in config["text_fields"]:
        text = getattr(record, field_name, None)
        if not text:
            continue

        record_id = getattr(record, "id", None)
        external_id = getattr(record, config["id_field"], str(record_id))
        for index, sub_text in enumerate(chunk_text(text, max_length=800, overlap=100)):
            preview = sub_text[:200] + "..." if len(sub_text) > 200 else sub_text
            chunks.append(
                {
                    "chunk_key": f"{domain}:{external_id}:{field_name}:{index}",
                    "record_id": record_id,
                    "source_field": field_name,
                    "chunk_order": index,
                    "content": sub_text,
                    "content_preview": preview,
                }
            )
    return chunks


def batch_embed(texts: List[str], embedding_batch_size: int = 64, embedding_workers: int = 1):
    return embedding_service.embed_batch(
        texts,
        batch_size=embedding_batch_size,
        num_workers=embedding_workers,
    )


def reset_domain_vectors(domain: str) -> None:
    """Reset vector_indexed flag and clear Milvus collection for a domain."""
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        result = db.query(TextChunk).filter(
            TextChunk.domain == domain,
            TextChunk.vector_indexed.is_(True),
        ).update({TextChunk.vector_indexed: False}, synchronize_session=False)
        db.commit()
        print(f"[Reset] {domain}: {result} chunks reset to vector_indexed=False")

    # Clear Milvus collection
    try:
        vector_store.rebuild(domain, embeddings=[])
        print(f"[Reset] {domain}: Milvus collection dropped and recreated")
    except Exception as e:
        print(f"[Reset] {domain}: Milvus clear failed (non-fatal): {e}")


def process_domain(
    domain: str,
    batch_size: int = 1000,
    embedding_batch_size: int = 64,
    embedding_workers: int = 1,
    limit: Optional[int] = None,
    sample: Optional[float] = None,
    reset: bool = False,
) -> None:
    if reset:
        reset_domain_vectors(domain)

    print(f"\n{'=' * 60}")
    print(f"Start processing [{domain}] into Milvus")
    print(f"{'=' * 60}")

    config = DOMAIN_CONFIG[domain]
    model = config["model"]
    engine = create_engine(settings.database_url)

    with Session(engine) as db:
        total_count = db.query(model).count()
        print(f"Total records: {total_count}")
        if limit:
            total_count = min(total_count, limit)
            print(f"Limit records: {total_count}")

        if sample and sample < 1.0:
            all_ids = [record.id for record in db.query(model.id).all()]
            sample_size = int(len(all_ids) * sample)
            selected_ids = random.sample(all_ids, sample_size)
            print(f"Random sample: {sample_size} records ({sample * 100:.1f}%)")
        else:
            selected_ids = None

        processed = 0
        skipped = 0
        total_chunks = 0
        total_vectors = 0
        start_time = time.time()
        offset = 0

        while offset < total_count:
            if selected_ids:
                batch_ids = selected_ids[offset : offset + batch_size]
                records = db.query(model).filter(model.id.in_(batch_ids)).all()
            else:
                records = db.query(model).offset(offset).limit(batch_size).all()

            if not records:
                break

            all_chunk_data = []
            for record in records:
                all_chunk_data.extend(build_record_chunks(domain, record))

            if not all_chunk_data:
                processed += len(records)
                offset += batch_size
                continue

            try:
                insert_sql = text(
                    """
                    INSERT IGNORE INTO text_chunks
                        (domain, record_id, chunk_key, source_field, chunk_order,
                         content, content_preview, embedding_model, vector_indexed, metadata_json)
                    VALUES (:domain, :record_id, :chunk_key, :source_field, :chunk_order,
                            :content, :content_preview, :embedding_model, :vector_indexed, :metadata_json)
                    """
                )
                metadata = json.dumps({"domain": domain}, ensure_ascii=False)
                params = [
                    {
                        "domain": domain,
                        "record_id": item["record_id"],
                        "chunk_key": item["chunk_key"],
                        "source_field": item["source_field"],
                        "chunk_order": item["chunk_order"],
                        "content": item["content"],
                        "content_preview": item["content_preview"],
                        "embedding_model": settings.embedding_model or "hashing",
                        "vector_indexed": False,
                        "metadata_json": metadata,
                    }
                    for item in all_chunk_data
                ]
                result = db.execute(insert_sql, params)
                db.commit()
                inserted_count = result.rowcount
                skipped += len(all_chunk_data) - inserted_count
                total_chunks += inserted_count
            except Exception as exc:
                print(f"  Failed to write text chunks: {exc}")
                db.rollback()
                processed += len(records)
                offset += batch_size
                continue

            if inserted_count == 0:
                processed += len(records)
                offset += batch_size
                continue

            inserted_keys = [item["chunk_key"] for item in all_chunk_data]
            db_chunks = db.query(TextChunk).filter(
                TextChunk.chunk_key.in_(inserted_keys),
                TextChunk.vector_indexed.is_(False),
            ).all()

            if not db_chunks:
                processed += len(records)
                offset += batch_size
                continue

            texts = [chunk.content for chunk in db_chunks]
            print(f"  Generating {len(texts)} embeddings (embedding batch={embedding_batch_size})...")
            vectors = batch_embed(texts, embedding_batch_size, embedding_workers)

            embeddings = []
            for chunk, vector in zip(db_chunks, vectors):
                embeddings.append(
                    {
                        "chunk_id": chunk.id,
                        "record_id": chunk.record_id,
                        "source_field": chunk.source_field,
                        "metadata": {"chunk_key": chunk.chunk_key},
                        "vector": vector.tolist() if hasattr(vector, "tolist") else vector,
                    }
                )

            vector_store.upsert(domain, embeddings)
            total_vectors += len(embeddings)

            chunk_ids = [chunk.id for chunk in db_chunks]
            db.query(TextChunk).filter(TextChunk.id.in_(chunk_ids)).update(
                {TextChunk.vector_indexed: True},
                synchronize_session=False,
            )
            db.commit()

            processed += len(records)
            elapsed = time.time() - start_time
            speed = processed / elapsed if elapsed > 0 else 0.0
            eta = (total_count - processed) / speed if speed > 0 else 0.0
            if processed % 100 == 0:
                print(
                    f"  Processed {processed}/{total_count} records | "
                    f"chunks={total_chunks} | vectors={total_vectors} | "
                    f"speed={speed:.0f}/s | eta={eta / 60:.0f}m"
                )

            offset += batch_size
            if limit and processed >= limit:
                break

        elapsed_total = time.time() - start_time
        print(f"\n[{domain}] completed")
        print(f"  records: {processed}")
        print(f"  new chunks: {total_chunks}")
        print(f"  skipped chunks: {skipped}")
        print(f"  vectors: {total_vectors}")
        print(f"  elapsed: {elapsed_total / 60:.1f} min")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Milvus vector collections from database records.")
    parser.add_argument("--domain", choices=["policy", "tender", "enterprise"], help="Only process one domain.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Database records per batch.")
    parser.add_argument("--embedding-batch", type=int, default=64, help="Embedding batch size.")
    parser.add_argument("--embedding-workers", type=int, default=1, help="Embedding worker processes.")
    parser.add_argument("--limit", type=int, help="Limit the number of records for testing.")
    parser.add_argument("--sample", type=float, help="Sample ratio for fast testing, range 0-1.")
    parser.add_argument("--reset", action="store_true", help="Reset vector_indexed and clear Milvus before processing.")
    parser.add_argument("--only-reset", action="store_true", help="Only reset vector_indexed and clear Milvus, do not process.")
    args = parser.parse_args()

    if args.only_reset:
        domains = [args.domain] if args.domain else ["policy", "tender", "enterprise"]
        for domain in domains:
            reset_domain_vectors(domain)
        print("Reset done.")
        return

    vector_store.ensure_collections()
    print("=" * 60)
    print("Milvus vector indexing script")
    print(f"Embedding model: {settings.embedding_model or 'hashing'}")
    print(f"Embedding dimension: {settings.embedding_dimension}")
    print(f"Embedding batch: {args.embedding_batch}")
    print(f"Milvus endpoint: {vector_store.uri}")
    print("=" * 60)

    domains = [args.domain] if args.domain else ["policy", "tender", "enterprise"]
    for domain in domains:
        try:
            process_domain(
                domain,
                batch_size=args.batch_size,
                embedding_batch_size=args.embedding_batch,
                embedding_workers=args.embedding_workers,
                limit=args.limit,
                sample=args.sample,
                reset=args.reset,
            )
        except Exception as exc:
            print(f"\n[{domain}] failed: {exc}")
            raise

    print("\n" + "=" * 60)
    print("All domains completed")
    print("=" * 60)


if __name__ == "__main__":
    main()
