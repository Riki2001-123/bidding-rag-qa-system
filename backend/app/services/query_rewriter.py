"""
Query Rewrite — 查询改写模块。

负责在检索前对用户查询进行预处理，提升检索质量。
主要能力：
1. 指代消解（Coreference Resolution）：将"它""那个项目"等指代词替换为具体实体
2. 查询扩展（Query Expansion）：对过于简短的查询补充语义信息
3. 查询分解（Query Decomposition）：将复杂问题拆分为子问题

设计原则：
- 轻量级：用 LLM 做一次性改写，不引入额外模型
- 失败安全：改写失败时回退到原始查询
- 延迟控制：异步调用，不阻塞主流程
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from app.services.llm import close_llm_client, get_llm_client


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class RewrittenQuery:
    """改写后的查询结果。"""

    rewritten: str           # 改写后的查询文本
    is_coreference: bool     # 是否进行了指代消解
    is_decomposed: bool      # 是否进行了查询分解
    sub_queries: List[str]   # 分解后的子查询（如有）
    reasoning: str           # 改写理由


# ── 核心 Prompt ────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "你是招投标采购问答系统的查询改写专家。你的唯一职责是改写用户查询，使其更适合检索系统理解。\n"
    "你不回答任何业务问题，只改写查询。\n\n"
    "## 当前对话历史\n"
    "{history}\n\n"
    "## 改写规则\n\n"
    "### 1. 指代消解（最高优先级）\n"
    "将以下代词/指代替换为历史中对应的实体名称：\n"
    "- \"它\"\"这家\"\"那家\" → 替换为企业全称\n"
    "- \"那个项目\"\"这个项目\"\"第一条\" → 替换为项目全称\n"
    "- \"那个政策\"\"这个条例\" → 替换为政策/法规全称\n"
    "- \"它的\"\"那家公司的\" → 替换为对应主体的所有格\n\n"
    "**关键**：只在历史中能明确找到对应实体时才替换。如果指代对象不明确，保留原词并输出 is_coreference=false。\n\n"
    "### 2. 简短查询扩展\n"
    "当查询过于简短（<=6个字符）且不含明确实体名称时，\n"
    "在不改变用户原意的前提下补充语义关键词。\n"
    "例如：\"能投吗\" → \"该项目是否符合投标资格条件\"\n\n"
    "### 3. 查询分解\n"
    "当查询包含多个独立问题时，拆分为子查询。\n"
    "例如：\"这家公司中标过哪些项目，资质怎么样\" → 两个子查询\n"
    "**注意**：不要过度分解，只有当问题确实包含多个独立子问题时才分解。\n"
    "如果子问题之间有依赖关系（如\"中标金额最高的那个项目是哪个公司中标的\"），\n"
    "不要分解，作为单一查询处理。\n\n"
    "## 输出格式\n\n"
    "你必须输出一个合法的 JSON 对象，不包含任何其他文本：\n"
    "```json\n"
    "{\n"
    "  \"rewritten\": \"改写后的查询\",\n"
    "  \"is_coreference\": true/false,\n"
    "  \"is_decomposed\": true/false,\n"
    "  \"sub_queries\": [\"子查询1\", \"子查询2\"],\n"
    "  \"reasoning\": \"改写理由\"\n"
    "}\n"
    "```\n\n"
    "如果查询不需要改写（无指代、长度合理、无分解需求），rewritten 保持原文，"
    "is_coreference=false, is_decomposed=false, sub_queries=[]。"
)

_USER_TEMPLATE = (
    "当前用户查询：{question}\n\n"
    "请改写上述查询。"
)


# ── 辅助函数 ──────────────────────────────────────────────────


def _build_history_text(history_messages: Sequence[dict], max_turns: int = 4) -> str:
    """将历史消息格式化为 prompt 可用的文本。"""
    if not history_messages:
        return "（无历史对话，这是首轮提问）"

    recent = history_messages[-max_turns:]
    lines = []
    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            # 截断过长的回答，保留关键信息
            preview = content[:300] + ("..." if len(content) > 300 else "")
            lines.append(f"助手：{preview}")
    return "\n".join(lines)


def _parse_rewrite_result(text: str) -> Optional[Dict]:
    """解析 LLM 返回的 JSON 结果。"""
    from app.services.json_utils import extract_json_object
    return extract_json_object(text)


# ── 核心 API ──────────────────────────────────────────────────


def rewrite_query(
    question: str,
    history_messages: Optional[Sequence[dict]] = None,
    entity_context: str = "",
) -> RewrittenQuery:
    """
    改写用户查询。

    Args:
        question: 用户原始问题
        history_messages: 历史对话消息列表，用于指代消解
        entity_context: 实体记忆上下文（P2），用于提升指代消解准确性

    Returns:
        RewrittenQuery: 改写后的查询结果
    """
    # 无历史对话时，不需要做指代消解
    if not history_messages:
        return RewrittenQuery(
            rewritten=question,
            is_coreference=False,
            is_decomposed=False,
            sub_queries=[],
            reasoning="无历史对话，跳过改写",
        )

    # ── P0-2: 指代词预判 ──
    # 如果查询中不包含任何指代词/代词，且长度合理（>6字），不需要 LLM 改写
    # 只在有指代消解需求或查询过短时才调 LLM，避免无意义的 1~3 秒开销
    _COREFERENCE_INDICATORS = (
        "它", "它们", "这个", "那个", "这家", "那家",
        "第一条", "第二条", "第三条", "上一条",
        "前者", "后者", "其", "该", "上述", "之前",
        "这个项目", "那个项目", "这个公司", "那家公司",
        "这条政策", "那个政策", "这份", "那份",
    )
    _has_coreference = any(ind in question for ind in _COREFERENCE_INDICATORS)
    _is_short_query = len(question.strip()) <= 6
    if not _has_coreference and not _is_short_query:
        return RewrittenQuery(
            rewritten=question,
            is_coreference=False,
            is_decomposed=False,
            sub_queries=[],
            reasoning="无指代词且查询长度合理，跳过改写",
        )

    llm = get_llm_client()
    if llm is None:
        return RewrittenQuery(
            rewritten=question,
            is_coreference=False,
            is_decomposed=False,
            sub_queries=[],
            reasoning="LLM 不可用，跳过改写",
        )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        history_text = _build_history_text(history_messages)

        # ── P2: 注入实体记忆 ──
        # 将已识别的实体信息附加到历史文本中，帮助 LLM 更准确地进行指代消解
        if entity_context:
            history_text = f"{history_text}\n\n{entity_context}"

        system_content = _SYSTEM_PROMPT.format(history=history_text)
        user_content = _USER_TEMPLATE.format(question=question)

        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=user_content),
        ]

        result = llm.invoke(messages)
        parsed = _parse_rewrite_result(getattr(result, "content", "") or "")

        if parsed is None:
            return RewrittenQuery(
                rewritten=question,
                is_coreference=False,
                is_decomposed=False,
                sub_queries=[],
                reasoning="改写结果解析失败，使用原始查询",
            )

        rewritten = str(parsed.get("rewritten", question)).strip()
        if not rewritten:
            rewritten = question

        is_coreference = bool(parsed.get("is_coreference", False))
        is_decomposed = bool(parsed.get("is_decomposed", False))
        sub_queries = []
        if is_decomposed:
            raw_subs = parsed.get("sub_queries", [])
            if isinstance(raw_subs, list):
                sub_queries = [str(sq).strip() for sq in raw_subs if str(sq).strip()]
            if not sub_queries:
                is_decomposed = False

        reasoning = str(parsed.get("reasoning", "")).strip()

        return RewrittenQuery(
            rewritten=rewritten,
            is_coreference=is_coreference,
            is_decomposed=is_decomposed,
            sub_queries=sub_queries,
            reasoning=reasoning or "查询改写完成",
        )

    except Exception as exc:
        print(f"[QueryRewrite] 改写失败，回退到原始查询: {exc}")
        return RewrittenQuery(
            rewritten=question,
            is_coreference=False,
            is_decomposed=False,
            sub_queries=[],
            reasoning=f"改写异常: {exc}",
        )
    finally:
        close_llm_client(llm)
