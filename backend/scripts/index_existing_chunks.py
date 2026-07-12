"""
Index existing text_chunks into Milvus without rebuilding chunk rows.

Usage:
    python backend/scripts/index_existing_chunks.py --domain policy
    python backend/scripts/index_existing_chunks.py --domain enterprise --limit 1000
    python backend/scripts/index_existing_chunks.py --domain tender --force
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.entities import TextChunk
from app.services.embeddings import embedding_service
from app.services.vector_store import vector_store


VALID_DOMAINS = ("policy", "tender", "enterprise")


def chunk_counts(domain: Optional[str] = None) -> dict:
    with SessionLocal() as db:
        domains = [domain] if domain else db.scalars(select(TextChunk.domain).distinct()).all()
        output = {}
        for name in domains:
            total = db.scalar(select(func.count(TextChunk.id)).where(TextChunk.domain == name)) or 0
            indexed = db.scalar(
                select(func.count(TextChunk.id)).where(
                    TextChunk.domain == name,
                    TextChunk.vector_indexed.is_(True),
                )
            ) or 0
            output[name] = {"chunks": int(total), "vector_indexed": int(indexed), "missing": int(total - indexed)}
        return output


def index_domain(
    domain: str,
    *,
    batch_size: int,
    embedding_batch_size: int,
    embedding_workers: int,
    limit: Optional[int],
    force: bool,
) -> int:
    vector_store.ensure_collections([domain])
    indexed = 0
    last_id = 0

    while True:
        remaining = None if limit is None else limit - indexed
        if remaining is not None and remaining <= 0:
            break
        current_batch_size = min(batch_size, remaining) if remaining is not None else batch_size

        with SessionLocal() as db:
            stmt = (
                select(TextChunk)
                .where(TextChunk.domain == domain, TextChunk.id > last_id)
                .order_by(TextChunk.id.asc())
                .limit(current_batch_size)
            )
            if not force:
                stmt = stmt.where(TextChunk.vector_indexed.is_(False))
            chunks = db.scalars(stmt).all()
            if not chunks:
                break

            vectors = embedding_service.embed_batch(
                [chunk.content for chunk in chunks],
                batch_size=embedding_batch_size,
                num_workers=embedding_workers,
            )
            vector_store.upsert(
                domain,
                [
                    {
                        "chunk_id": chunk.id,
                        "record_id": chunk.record_id,
                        "source_field": chunk.source_field,
                        "metadata": {"domain": domain, "chunk_key": chunk.chunk_key},
                        "vector": vector,
                    }
                    for chunk, vector in zip(chunks, vectors)
                ],
            )
            for chunk in chunks:
                chunk.vector_indexed = True
            db.commit()
            indexed += len(chunks)
            last_id = chunks[-1].id
            print(f"[Index] {domain}: indexed {indexed} chunks in this run; last_id={last_id}", flush=True)

    return indexed


def main() -> None:
    parser = argparse.ArgumentParser(description="Index existing text_chunks into Milvus.")
    parser.add_argument("--domain", choices=VALID_DOMAINS)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--embedding-batch", type=int, default=64)
    parser.add_argument("--embedding-workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="Re-index chunks even when vector_indexed is already true.")
    parser.add_argument("--status", action="store_true", help="Only print current chunk/vector_indexed counts.")
    args = parser.parse_args()

    if args.status:
        print(chunk_counts(args.domain))
        return

    domains: List[str] = [args.domain] if args.domain else list(VALID_DOMAINS)
    for domain in domains:
        indexed = index_domain(
            domain,
            batch_size=args.batch_size,
            embedding_batch_size=args.embedding_batch,
            embedding_workers=args.embedding_workers,
            limit=args.limit,
            force=args.force,
        )
        print(f"[Index] {domain}: done, indexed {indexed} chunks")
    print(chunk_counts(args.domain))


if __name__ == "__main__":
    main()
