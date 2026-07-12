"""
BM25 keyword retrieval over text chunks.

This index is built per domain from the TextChunk table and is used
alongside vector retrieval for hybrid search.
"""

from typing import Dict, List, Optional

try:
    import jieba
except Exception:  # pragma: no cover
    jieba = None

try:
    from rank_bm25 import BM25Okapi
except Exception:  # pragma: no cover
    BM25Okapi = None


class BM25Index:
    """Manage one BM25 index per domain."""

    def __init__(self):
        self._indices: Dict[str, "BM25Okapi"] = {}
        self._chunk_maps: Dict[str, Dict[int, int]] = {}
        self._position_chunk_ids: Dict[str, List[int]] = {}
        self._ready = BM25Okapi is not None and jieba is not None

    @property
    def available(self) -> bool:
        return self._ready

    def build_index(self, domain: str, chunks: list) -> None:
        if not self._ready or not chunks:
            return

        corpus: List[List[str]] = []
        id_to_pos: Dict[int, int] = {}
        position_chunk_ids: List[int] = []
        for idx, chunk in enumerate(chunks):
            tokens = list(jieba.cut(chunk.content or ""))
            corpus.append(tokens)
            id_to_pos[chunk.id] = idx
            position_chunk_ids.append(chunk.id)

        if not corpus:
            return

        print(f"[BM25] start building index for {domain}: {len(chunks)} chunks")
        self._indices[domain] = BM25Okapi(corpus)
        self._chunk_maps[domain] = id_to_pos
        self._position_chunk_ids[domain] = position_chunk_ids
        print(f"[BM25] index ready for {domain}: {len(chunks)} chunks")

    def search(self, domain: str, query: str, top_k: int = 10) -> List[dict]:
        if not self._ready:
            return []

        index = self._indices.get(domain)
        chunk_map = self._chunk_maps.get(domain)
        position_chunk_ids = self._position_chunk_ids.get(domain)
        if index is None or chunk_map is None or position_chunk_ids is None:
            return []

        tokens = list(jieba.cut(query or ""))
        scores = index.get_scores(tokens)

        scored = [
            {"chunk_id": position_chunk_ids[pos], "score": float(score)}
            for pos, score in enumerate(scores)
            if score > 0
        ]
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def clear(self, domain: Optional[str] = None) -> None:
        if domain:
            self._indices.pop(domain, None)
            self._chunk_maps.pop(domain, None)
            self._position_chunk_ids.pop(domain, None)
            return

        self._indices.clear()
        self._chunk_maps.clear()
        self._position_chunk_ids.clear()


bm25_index = BM25Index()
