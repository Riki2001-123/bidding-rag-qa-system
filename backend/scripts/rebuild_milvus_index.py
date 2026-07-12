"""
Rebuild Milvus collections from the current text_chunks table.

Usage:
    cd D:\\python\\PythonProject\\RAG+LLMProject
    python -m backend.scripts.rebuild_milvus_index

Optional:
    python -m backend.scripts.rebuild_milvus_index --domain policy
    python -m backend.scripts.rebuild_milvus_index --embedding-batch 64
"""

import argparse
import sys
import threading
import time
from collections import defaultdict
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


DOMAINS = ("policy", "tender", "enterprise")


class ProgressHeartbeat:
    def __init__(self, domain: str, total: int, interval_seconds: int) -> None:
        self.domain = domain
        self.total = total
        self.interval_seconds = max(int(interval_seconds), 1)
        self.processed = 0
        self.batch_index = 0
        self.batch_size = 0
        self.batch_done = 0
        self.batch_total = 0
        self.stage = "starting"
        self.elapsed_seconds = 0.0
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def update(
        self,
        *,
        processed: Optional[int] = None,
        batch_index: Optional[int] = None,
        batch_size: Optional[int] = None,
        batch_done: Optional[int] = None,
        batch_total: Optional[int] = None,
        stage: Optional[str] = None,
        elapsed_seconds: Optional[float] = None,
    ) -> None:
        if processed is not None:
            self.processed = processed
        if batch_index is not None:
            self.batch_index = batch_index
        if batch_size is not None:
            self.batch_size = batch_size
        if batch_done is not None:
            self.batch_done = batch_done
        if batch_total is not None:
            self.batch_total = batch_total
        if stage is not None:
            self.stage = stage
        if elapsed_seconds is not None:
            self.elapsed_seconds = elapsed_seconds

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            percent = self.processed / self.total * 100 if self.total else 100.0
            detail = (
                f"batch={self.batch_index} | batch_progress={self.batch_done}/{self.batch_total}"
                if self.batch_total
                else f"batch={self.batch_index} | batch_size={self.batch_size}"
            )
            print(
                f"[Milvus] {self.domain}: heartbeat | stage={self.stage} | "
                f"processed={self.processed}/{self.total} ({percent:.2f}%) | "
                f"{detail} | elapsed={self.elapsed_seconds:.1f}s",
                flush=True,
            )


def _domain_chunk_total(domain: str) -> int:
    with SessionLocal() as db:
        total = db.scalar(
            select(func.count())
            .select_from(TextChunk)
            .where(TextChunk.domain == domain)
        )
    return int(total or 0)


def _load_chunk_batch(domain: str, last_chunk_id: int, read_batch_size: int) -> List[TextChunk]:
    with SessionLocal() as db:
        return db.scalars(
            select(TextChunk)
            .where(TextChunk.domain == domain, TextChunk.id > last_chunk_id)
            .order_by(TextChunk.id.asc())
            .limit(read_batch_size)
        ).all()


def _get_chunk_id_at_offset(domain: str, offset: int) -> int:
    """Get the chunk_id at a given row offset (0-indexed) for the domain."""
    with SessionLocal() as db:
        row = db.scalars(
            select(TextChunk.id)
            .where(TextChunk.domain == domain)
            .order_by(TextChunk.id.asc())
            .offset(offset)
            .limit(1)
        ).first()
    return int(row) if row else 0


def rebuild_domain(
    domain: str,
    embedding_batch_size: int,
    embedding_workers: int,
    read_batch_size: int,
    heartbeat_seconds: int,
    skip_rebuild: bool = False,
    skip_count: int = 0,
) -> int:
    total = _domain_chunk_total(domain)
    if not skip_rebuild:
        vector_store.rebuild(domain, [])
    else:
        # Skip already processed chunks
        skip_count = min(skip_count, total)
        print(f"[Milvus] {domain}: skipping {skip_count} already processed chunks", flush=True)
    if total == 0:
        print(f"[Milvus] {domain}: no text chunks to rebuild", flush=True)
        return 0

    print(f"[Milvus] {domain}: rebuilding {total} chunks", flush=True)
    start_time = time.time()
    processed = 0
    if skip_rebuild and skip_count > 0:
        last_chunk_id = _get_chunk_id_at_offset(domain, skip_count)
        processed = skip_count
        print(f"[Milvus] {domain}: resuming from chunk_id={last_chunk_id}, {skip_count} already done", flush=True)
    else:
        last_chunk_id = 0
    batch_index = 0
    heartbeat = ProgressHeartbeat(domain, total, heartbeat_seconds)
    heartbeat.start()

    try:
        while True:
            chunks = _load_chunk_batch(domain, last_chunk_id, read_batch_size)
            if not chunks:
                break
            batch_index += 1
            heartbeat.update(
                processed=processed,
                batch_index=batch_index,
                batch_size=len(chunks),
                batch_done=0,
                batch_total=len(chunks),
                stage="encoding",
                elapsed_seconds=time.time() - start_time,
            )
            print(
                f"[Milvus] {domain}: encoding batch {batch_index} | "
                f"size={len(chunks)} | processed={processed}/{total}",
                flush=True,
            )

            def _log_embedding_progress(done: int, current_total: int) -> None:
                heartbeat.update(
                    processed=processed,
                    batch_index=batch_index,
                    batch_size=len(chunks),
                    batch_done=done,
                    batch_total=current_total,
                    stage="encoding",
                    elapsed_seconds=time.time() - start_time,
                )
                print(
                    f"[Milvus] {domain}: batch {batch_index} embedding {done}/{current_total}",
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

            heartbeat.update(
                processed=processed,
                batch_index=batch_index,
                batch_size=len(chunks),
                batch_done=len(chunks),
                batch_total=len(chunks),
                stage="upserting",
                elapsed_seconds=time.time() - start_time,
            )
            vector_store.upsert(domain, embeddings)
            processed += len(embeddings)
            last_chunk_id = chunks[-1].id
            percent = processed / total * 100 if total else 100.0
            elapsed = time.time() - start_time
            heartbeat.update(
                processed=processed,
                batch_index=batch_index,
                batch_size=len(chunks),
                batch_done=len(chunks),
                batch_total=len(chunks),
                stage="completed_batch",
                elapsed_seconds=elapsed,
            )
            print(
                f"[Milvus] {domain}: {processed}/{total} vectors ({percent:.2f}%) | "
                f"batch={len(embeddings)} | elapsed={elapsed:.1f}s",
                flush=True,
            )
    finally:
        heartbeat.stop()

    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild Milvus collections from database text chunks.")
    parser.add_argument("--domain", choices=DOMAINS, help="Only rebuild one domain.")
    parser.add_argument("--embedding-batch", type=int, default=64, help="Embedding batch size.")
    parser.add_argument("--embedding-workers", type=int, default=1, help="Embedding worker processes for sentence-transformers.")
    parser.add_argument("--read-batch", type=int, default=128, help="Text chunks to load and write per batch.")
    parser.add_argument("--heartbeat-seconds", type=int, default=60, help="Progress heartbeat interval in seconds.")
    parser.add_argument("--skip-rebuild", action="store_true", help="Skip clearing Milvus collection (resume mode).")
    parser.add_argument("--skip-count", type=int, default=0, help="Number of chunks already processed (used with --skip-rebuild).")
    args = parser.parse_args()

    vector_store.ensure_collections()
    domains = [args.domain] if args.domain else list(DOMAINS)
    rebuilt = defaultdict(int)
    for domain in domains:
        count = rebuild_domain(
            domain,
            args.embedding_batch,
            args.embedding_workers,
            args.read_batch,
            args.heartbeat_seconds,
            skip_rebuild=args.skip_rebuild,
            skip_count=args.skip_count,
        )
        rebuilt[domain] = count
        print(f"[Milvus] rebuilt {domain}: {count} vectors", flush=True)

    total = sum(rebuilt.values())
    print(f"[Milvus] done: {total} vectors across {len(domains)} domain(s)", flush=True)


if __name__ == "__main__":
    main()
