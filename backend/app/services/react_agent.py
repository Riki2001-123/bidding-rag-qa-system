"""
ReAct Agent — 自研多步推理 Agent 循环。

基于 ReAct (Reasoning + Acting) 范式，实现：
- Thought-Action-Observation 循环
- 工具调用（SQL 精确查询 + 向量检索 + BM25 关键词检索）
- CRAG 风格的文档相关性判定
- 自适应复杂度路由（简单问题走快速通道，复杂问题走多步推理）

设计原则：
- 纯 Python 实现，不依赖 LangChain Agent 模块
- 轻量级：最多 3 轮循环，防止无限推理
- 失败安全：工具调用失败时优雅降级到标准 RAG 流程
- 延迟可控：工具定义紧凑，prompt 精简
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.models.entities import User
from app.services.llm import close_llm_client, get_llm_client
from app.services.retrieval import search_domain
from app.services.sql_agent import detect_sql_intent, execute_sql_intent


# ── 常量 ──────────────────────────────────────────────────────

MAX_REACT_STEPS = 3  # 最大推理轮次


# ── 工具注册 ──────────────────────────────────────────────────


@dataclass
class ToolDefinition:
    """工具定义，遵循 OpenAI function calling 格式。"""
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class ToolResult:
    """工具执行结果。"""
    tool_name: str
    success: bool
    data: Any
    error: Optional[str] = None


# ── 工具集 ────────────────────────────────────────────────────

def _build_tools_schema() -> List[dict]:
    """构建工具定义列表，供 LLM function calling 使用。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "sql_query",
                "description": (
                    "执行结构化 SQL 查询，适用于聚合统计、排名、计数、金额筛选、排序等问题。"
                    "支持多种查询类型：金额排名(top_by_amount)、金额筛选(filter_by_amount)、"
                    "中标次数统计(top_by_bid_count)、计数(count)、按阶段列表(list_by_stage)、通用列表(list)。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_type": {
                            "type": "string",
                            "enum": [
                                "top_by_amount",
                                "filter_by_amount",
                                "top_by_bid_count",
                                "count",
                                "list_by_stage",
                                "list",
                            ],
                            "description": (
                                "查询类型：\n"
                                "- top_by_amount: 金额排名，需 params.top_n\n"
                                "- filter_by_amount: 金额筛选，需 params.filter_op(gte/lte) 和 params.filter_amount\n"
                                "- top_by_bid_count: 中标次数排名，需 params.top_n\n"
                                "- count: 计数统计\n"
                                "- list_by_stage: 按阶段列表，需 params.stage\n"
                                "- list: 通用列表"
                            ),
                        },
                        "domain": {
                            "type": "string",
                            "enum": ["tender", "enterprise", "policy"],
                            "description": "业务领域",
                        },
                        "params": {
                            "type": "object",
                            "description": "查询参数，如 top_n, stage, filter_op, filter_amount 等",
                            "properties": {
                                "top_n": {"type": "integer", "description": "返回数量，默认 3"},
                                "order": {"type": "string", "enum": ["asc", "desc"], "description": "排序方向"},
                                "field": {"type": "string", "description": "排序字段，如 bid_amount"},
                                "filter_op": {"type": "string", "enum": ["gte", "gt", "lte", "lt"], "description": "筛选操作符"},
                                "filter_amount": {"type": "number", "description": "筛选金额（元）"},
                                "stage": {"type": "string", "description": "阶段筛选，如 中标/招标"},
                                "filter_field": {"type": "string", "description": "过滤字段"},
                                "filter_value": {"type": "string", "description": "过滤值"},
                            },
                        },
                    },
                    "required": ["query_type", "domain"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "semantic_search",
                "description": (
                    "语义检索知识库，适用于事实查询、政策解读、企业信息等问题。"
                    "返回与查询语义最相关的文档片段。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "enum": ["tender", "enterprise", "policy"],
                            "description": "业务领域",
                        },
                        "query": {
                            "type": "string",
                            "description": "检索查询文本",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "返回结果数量，默认 10",
                            "default": 10,
                        },
                    },
                    "required": ["domain", "query"],
                },
            },
        },
    ]


def _execute_tool(
    tool_name: str,
    arguments: dict,
    db,
    user: User,
    question: str,
) -> ToolResult:
    """执行工具调用并返回结果。"""
    try:
        if tool_name == "sql_query":
            return _execute_sql_tool(arguments, db, user, question)
        elif tool_name == "semantic_search":
            return _execute_search_tool(arguments, db, user)
        else:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                data=None,
                error=f"未知工具: {tool_name}",
            )
    except Exception as exc:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            data=None,
            error=f"工具执行异常: {exc}",
        )


def _execute_sql_tool(arguments: dict, db, user: User, question: str) -> ToolResult:
    """执行 SQL 查询工具。"""
    query_type = arguments.get("query_type", "")
    domain = arguments.get("domain", "")

    # 构建 intent dict
    intent = {"type": query_type, "domain": domain}
    params = arguments.get("params", {})
    intent.update(params)

    result = execute_sql_intent(db, user, intent, question)
    if result is None or not result.success:
        return ToolResult(
            tool_name="sql_query",
            success=False,
            data=None,
            error="SQL 查询未返回有效结果",
        )

    return ToolResult(
        tool_name="sql_query",
        success=True,
        data={
            "answer": result.answer_text,
            "row_count": len(result.data),
            "data": result.data[:10],  # 限制返回数据量
        },
    )


def _execute_search_tool(arguments: dict, db, user: User) -> ToolResult:
    """执行语义检索工具。"""
    domain = arguments.get("domain", "tender")
    query = arguments.get("query", "")
    top_k = min(arguments.get("top_k", 10), 20)

    if not query:
        return ToolResult(
            tool_name="semantic_search",
            success=False,
            data=None,
            error="查询文本为空",
        )

    results = search_domain(db=db, domain=domain, user=user, q=query, top_k=top_k)

    if not results:
        return ToolResult(
            tool_name="semantic_search",
            success=True,
            data={"results": [], "message": "未找到相关文档"},
        )

    documents = []
    for item in results:
        documents.append({
            "title": item.title,
            "summary": (item.summary or "")[:300],
            "score": round(item.score, 4),
            "key_fields": item.key_fields,
        })

    return ToolResult(
        tool_name="semantic_search",
        success=True,
        data={"results": documents, "total": len(documents)},
    )


# ── ReAct Agent 核心 ──────────────────────────────────────────

# ReAct 系统提示词
_REACT_SYSTEM_PROMPT = (
    "你是招投标采购智能问答助手。你可以使用工具来获取信息，然后基于工具返回的结果回答用户问题。\n\n"
    "## 可用工具\n"
    "- sql_query：结构化 SQL 查询，适用于统计、排名、计数等数值型问题\n"
    "- semantic_search：语义检索知识库，适用于事实查询、政策解读、企业信息等\n\n"
    "## 推理策略\n"
    "1. 先思考用户问题需要什么信息\n"
    "2. 选择最合适的工具调用\n"
    "3. 分析工具返回的结果\n"
    "4. 如果结果不足，可以再调用一次工具（换关键词或换领域）\n"
    "5. 信息充分后，给出最终回答\n\n"
    "## 回答要求\n"
    "- 严格基于工具返回的数据回答，不编造\n"
    "- 涉及金额、日期、主体时，明确来源\n"
    "- 证据不足时明确说明\n"
    "- 用自然语言组织回答，不要模板化\n"
)


@dataclass
class ReActStep:
    """单步推理记录。"""
    step_num: int
    thought: Optional[str] = None
    tool_call: Optional[dict] = None
    tool_result: Optional[ToolResult] = None
    observation: Optional[str] = None


@dataclass
class ReActResult:
    """ReAct Agent 执行结果。"""
    answer: str
    steps: List[ReActStep] = field(default_factory=list)
    tool_calls_count: int = 0
    used_react: bool = False  # 是否真正使用了 ReAct 循环
    fallback_reason: Optional[str] = None


def _format_tool_result_for_llm(result: ToolResult) -> str:
    """将工具结果格式化为 LLM 可理解的文本。"""
    if not result.success:
        return f"[工具 {result.tool_name} 执行失败] {result.error}"

    if result.tool_name == "sql_query":
        data = result.data
        parts = [f"[SQL 查询结果]"]
        parts.append(data.get("answer", ""))
        if data.get("row_count", 0) > 0:
            parts.append(f"（共 {data['row_count']} 条记录）")
        return "\n".join(parts)

    if result.tool_name == "semantic_search":
        data = result.data
        parts = [f"[语义检索结果] 共找到 {data.get('total', 0)} 条相关文档："]
        for i, doc in enumerate(data.get("results", []), 1):
            parts.append(f"\n[文档{i}] 标题: {doc['title']}")
            parts.append(f"摘要: {doc['summary']}")
            parts.append(f"相关度: {doc['score']}")
        return "\n".join(parts)

    return json.dumps(result.data, ensure_ascii=False)


def run_react_agent(
    db,
    user: User,
    question: str,
    domain: str,
    history_messages: Optional[list] = None,
    max_steps: int = MAX_REACT_STEPS,
) -> ReActResult:
    """
    运行 ReAct Agent 循环。

    Args:
        db: 数据库 session
        user: 当前用户
        question: 用户问题
        domain: 业务领域（tender/policy/enterprise）
        history_messages: 历史对话
        max_steps: 最大推理步数

    Returns:
        ReActResult: 推理结果
    """
    llm = get_llm_client()
    if llm is None:
        return ReActResult(
            answer="",
            used_react=False,
            fallback_reason="LLM 不可用",
        )

    tools_schema = _build_tools_schema()
    steps: List[ReActStep] = []

    try:
        # 构建初始消息
        messages = [SystemMessage(content=_REACT_SYSTEM_PROMPT)]

        # 添加历史（截断）
        if history_messages:
            recent = history_messages[-4:]
            for msg in recent:
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

        messages.append(HumanMessage(content=f"当前业务域：{domain}\n用户角色：{user.role}\n\n用户问题：{question}"))

        for step_num in range(1, max_steps + 1):
            step = ReActStep(step_num=step_num)

            # 调用 LLM
            try:
                response = llm.invoke(messages, tools=tools_schema)
            except Exception as exc:
                # 如果工具调用格式不支持（某些模型不支持 function calling）
                print(f"[ReAct] LLM 调用失败（可能不支持 function calling），回退到标准流程: {exc}")
                return ReActResult(
                    answer="",
                    steps=steps,
                    used_react=False,
                    fallback_reason=f"LLM 不支持 function calling: {exc}",
                )

            # 检查是否有工具调用
            tool_calls = getattr(response, "tool_calls", None)

            if not tool_calls:
                # LLM 给出了最终回答
                step.thought = "推理完成，给出最终回答"
                step.observation = getattr(response, "content", "")
                steps.append(step)

                return ReActResult(
                    answer=getattr(response, "content", ""),
                    steps=steps,
                    tool_calls_count=sum(1 for s in steps if s.tool_call is not None),
                    used_react=True,
                )

            # 处理工具调用
            for tc in tool_calls:
                step.tool_call = {
                    "name": tc["name"],
                    "arguments": tc["args"],
                }

                tool_result = _execute_tool(
                    tool_name=tc["name"],
                    arguments=tc["args"],
                    db=db,
                    user=user,
                    question=question,
                )
                step.tool_result = tool_result
                step.observation = _format_tool_result_for_llm(tool_result)

            steps.append(step)

            # 将 assistant 消息和工具结果追加到对话
            messages.append(response)

            # 追加工具结果消息
            for tc in tool_calls:
                # 找到对应的 tool_result
                tc_name = tc["name"]
                matching_result = next(
                    (s.tool_result for s in steps if s.tool_call and s.tool_call["name"] == tc_name),
                    ToolResult(tool_name=tc_name, success=False, data=None, error="未找到结果"),
                )
                result_content = _format_tool_result_for_llm(matching_result)
                from langchain_core.messages import ToolMessage
                messages.append(ToolMessage(
                    tool_call_id=tc["id"],
                    content=result_content,
                ))

        # 达到最大步数，强制生成最终回答
        messages.append(HumanMessage(content="请基于以上所有工具返回的信息，直接给出最终回答。"))
        final_response = llm.invoke(messages)
        steps.append(ReActStep(
            step_num=max_steps + 1,
            thought="达到最大推理步数，生成最终回答",
            observation=getattr(final_response, "content", ""),
        ))

        return ReActResult(
            answer=getattr(final_response, "content", ""),
            steps=steps,
            tool_calls_count=sum(1 for s in steps if s.tool_call is not None),
            used_react=True,
            fallback_reason="达到最大推理步数",
        )

    except Exception as exc:
        print(f"[ReAct] 循环异常: {exc}")
        return ReActResult(
            answer="",
            steps=steps,
            used_react=False,
            fallback_reason=f"ReAct 循环异常: {exc}",
        )
    finally:
        close_llm_client(llm)


# ── 复杂度评估 ────────────────────────────────────────────────

# 简单查询特征（不需要 ReAct 的场景）
_SIMPLE_QUERY_PATTERNS = (
    # 单一实体查询
    r"^(.{0,4}(什么|是谁|介绍|情况).{0,20})$",
    # 简单判断
    r"^(.{0,10}(是不是|是否|能不能).{0,15})$",
    # 单一数字查询
    r"^(.{0,10}(多少|几个|金额).{0,15})$",
)

# 复杂查询特征（需要 ReAct 的场景）
_COMPLEX_QUERY_INDICATORS = (
    # 多步推理
    "哪几个", "分别", "各自", "各自对应",
    # 比较
    "区别", "不同", "对比", "比较", "哪个更好", "哪个更",
    # 关联分析
    "关联", "关系", "参与过", "中标过哪些",
    # 多维度
    "同时", "并且", "既", "综合", "汇总",
    # 排序+筛选组合
    "排名前", "最高", "最低",
    # 数值筛选
    "超过", "以上", "以下", "大于", "小于", "不低于", "不超过",
    # 排序
    "从高到低", "从低到高", "排列", "排序", "按.*排列", "按.*排序",
    # 聚合统计
    "次数最多", "次数最少", "前.*名", "前.*位", "有几个", "多少个",
    "最多", "最少", "总计", "平均", "总共",
)


def should_use_react(question: str, decision_confidence: float) -> bool:
    """
    判断是否应该使用 ReAct Agent。

    简单问题走快速通道（标准 RAG），复杂问题走 ReAct 多步推理。
    """
    q = (question or "").strip()

    # 极短查询（<=4 字）不使用 ReAct
    if len(q) <= 4:
        return False

    # 检查复杂度指标
    complex_score = sum(1 for indicator in _COMPLEX_QUERY_INDICATORS if indicator in q)

    # 包含多个独立问题的特征（问号分隔、顿号分隔的多个子问题）
    import re
    if q.count("？") >= 2 or q.count("?") >= 2:
        complex_score += 2
    if "，" in q and any(ind in q.split("，")[1] for ind in ("多少", "哪些", "哪个", "有没有")):
        complex_score += 1

    # 置信度过低时也倾向于用 ReAct（多工具辅助决策）
    if decision_confidence < 0.5:
        complex_score += 1

    return complex_score >= 1
