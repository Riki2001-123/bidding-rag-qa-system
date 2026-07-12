"""
语义相似度门控模块（P1）。

通过计算当前查询与上轮查询的 embedding 余弦相似度，避免多轮对话中的冗余检索。

三档分流策略：
- similarity > 0.85（高度相似）：直接复用上轮检索结果，跳过检索（省 0.5~1.5 秒）
- 0.6 <= similarity <= 0.85（中度相似）：用改写后的查询在已有结果中重新排序
- similarity < 0.6（差异较大）：执行全新检索

设计原则：
- 零额外开销：复用已有的 embedding_service，不引入新模型
- 失败安全：embedding 计算失败时回退到全新检索
- 自动降级：无上轮查询或上轮结果为空时直接执行全新检索
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from app.services.conversation_memory import conversation_memory


# ── 阈值配置 ──────────────────────────────────────────────────

REUSE_THRESHOLD = 0.85       # 高度相似：复用上轮结果
RERANK_THRESHOLD = 0.60      # 中度相似：重排上轮结果
# < RERANK_THRESHOLD：全新检索


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class RetrievalGateResult:
    """检索门控结果。"""
    action: str               # "reuse" | "rerank" | "full_search"
    similarity: float         # 余弦相似度
    cached_results: Optional[list] = None  # 复用或重排的缓存结果
    reason: str = ""


# ── 核心逻辑 ──────────────────────────────────────────────────


def _cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    """计算两个向量的余弦相似度。"""
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


def check_retrieval_gate(
    session_id: Optional[int],
    current_query: str,
    current_query_embedding: np.ndarray,
) -> RetrievalGateResult:
    """
    检查是否可以跳过或简化检索。

    Args:
        session_id: 会话 ID
        current_query: 当前（可能已改写的）查询
        current_query_embedding: 当前查询的 embedding 向量

    Returns:
        RetrievalGateResult: 门控结果，指示应该复用、重排还是全新检索
    """
    if session_id is None:
        return RetrievalGateResult(
            action="full_search",
            similarity=0.0,
            reason="无 session_id，执行全新检索",
        )

    memory = conversation_memory.get_memory(session_id)

    # 检查是否有上轮查询 embedding 和缓存结果
    if memory.last_query_embedding is None or memory.cached_results is None:
        return RetrievalGateResult(
            action="full_search",
            similarity=0.0,
            reason="无上轮查询缓存，执行全新检索",
        )

    # 计算余弦相似度
    last_embedding = np.asarray(memory.last_query_embedding, dtype="float32")
    similarity = _cosine_similarity(current_query_embedding, last_embedding)

    # 三档分流
    if similarity > REUSE_THRESHOLD:
        print(f"[RetrievalGate] similarity={similarity:.3f} > {REUSE_THRESHOLD}, 复用上轮结果", flush=True)
        return RetrievalGateResult(
            action="reuse",
            similarity=similarity,
            cached_results=memory.cached_results,
            reason=f"查询高度相似（{similarity:.2f}），复用上轮检索结果",
        )
    elif similarity >= RERANK_THRESHOLD:
        print(f"[RetrievalGate] similarity={similarity:.3f} ∈ [{RERANK_THRESHOLD}, {REUSE_THRESHOLD}], 需要重排", flush=True)
        return RetrievalGateResult(
            action="rerank",
            similarity=similarity,
            cached_results=memory.cached_results,
            reason=f"查询中度相似（{similarity:.2f}），在已有结果中重排",
        )
    else:
        print(f"[RetrievalGate] similarity={similarity:.3f} < {RERANK_THRESHOLD}, 全新检索", flush=True)
        return RetrievalGateResult(
            action="full_search",
            similarity=similarity,
            reason=f"查询差异较大（{similarity:.2f}），执行全新检索",
        )


def update_retrieval_cache(
    session_id: Optional[int],
    query: str,
    query_embedding: np.ndarray,
    results: list,
) -> None:
    """
    更新会话的检索缓存。

    Args:
        session_id: 会话 ID
        query: 当前查询
        query_embedding: 当前查询的 embedding
        results: 当前检索结果列表（RetrievedItem 或类似结构）
    """
    if session_id is None:
        return

    memory = conversation_memory.get_memory(session_id)
    conversation_memory.update_retrieval_cache(session_id, query_embedding, results)
