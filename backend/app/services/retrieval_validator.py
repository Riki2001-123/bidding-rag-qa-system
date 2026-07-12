"""
P0-1: 检索质量验证（Review Chain 模式）

在 collect_evidence() 之后、LLM 回答之前，对检索结果做轻量级相关性过滤。
纯规则判断，不调 LLM，延迟 < 5ms。
"""

from typing import List, Tuple

from app.services.retrieval import RetrievedItem


# ── 可配置阈值 ──
LOW_CONFIDENCE_THRESHOLD = 0.3  # 低于此分数标记为"低置信"
SUPPLEMENT_RATIO_THRESHOLD = 0.5  # 低置信文档占比超过此值时触发补充检索
DEFAULT_TOP_K_BOOST = 5  # 补充检索时额外拉取的文档数


def validate_retrieval_results(
    results: List[RetrievedItem],
    question: str = "",
) -> Tuple[List[RetrievedItem], bool, dict]:
    """对检索结果做质量验证和过滤。

    Args:
        results: 原始检索结果列表（已按分数降序）。
        question: 用户问题（预留，当前未使用）。

    Returns:
        (validated_results, need_supplement, stats)
        - validated_results: 验证后的结果列表（低置信文档排在后面）。
        - need_supplement: 是否建议补充检索。
        - stats: 验证统计信息，用于日志。
    """
    if not results:
        return [], False, {"total": 0, "low_confidence": 0, "ratio": 0.0}

    scored = []
    low_count = 0
    for item in results:
        score = item.score
        is_low = score < LOW_CONFIDENCE_THRESHOLD
        if is_low:
            low_count += 1
        scored.append((item, score, is_low))

    # 低置信文档排在后面，高置信排在前面
    scored.sort(key=lambda x: (x[2], -x[1]))

    ratio = low_count / len(scored)
    need_supplement = ratio > SUPPLEMENT_RATIO_THRESHOLD

    stats = {
        "total": len(scored),
        "low_confidence": low_count,
        "ratio": round(ratio, 3),
    }

    validated = [item for item, score, is_low in scored]

    if need_supplement:
        print(
            f"[RetrievalValidator] 质量预警: {low_count}/{len(scored)} 低置信(ratio={ratio:.1%}), "
            f"建议补充检索(top_k+{DEFAULT_TOP_K_BOOST})",
            flush=True,
        )

    return validated, need_supplement, stats
