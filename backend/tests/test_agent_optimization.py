"""
Agent 优化相关单元测试。

覆盖：
1. Query Rewrite（query_rewriter.py）
2. ReAct Agent（react_agent.py）
3. 自适应路由逻辑
"""

import pytest

from app.services.query_rewriter import (
    _build_history_text,
    _parse_rewrite_result,
    rewrite_query,
    RewrittenQuery,
)
from app.services.react_agent import (
    should_use_react,
    _execute_tool,
    _build_tools_schema,
    _format_tool_result_for_llm,
    ToolResult,
)


# ── Query Rewrite 测试 ──────────────────────────────────────


class TestBuildHistoryText:
    """测试历史消息格式化。"""

    def test_empty_history(self):
        result = _build_history_text([])
        assert "无历史对话" in result

    def test_none_history(self):
        result = _build_history_text(None)
        assert "无历史对话" in result

    def test_truncation(self):
        long_content = "x" * 500
        history = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": long_content},
        ]
        result = _build_history_text(history, max_turns=1)
        # 只保留最近1轮
        assert "问题" not in result
        assert "..." in result  # 长回答被截断

    def test_basic_format(self):
        history = [
            {"role": "user", "content": "招标项目有哪些？"},
            {"role": "assistant", "content": "共有3个项目"},
        ]
        result = _build_history_text(history)
        assert "用户：招标项目有哪些" in result
        assert "助手：共有3个项目" in result


class TestParseRewriteResult:
    """测试改写结果解析。"""

    def test_valid_json(self):
        text = '{"rewritten": "华为中标了哪些项目", "is_coreference": true, "is_decomposed": false, "sub_queries": [], "reasoning": "指代消解"}'
        result = _parse_rewrite_result(text)
        assert result is not None
        assert result["rewritten"] == "华为中标了哪些项目"
        assert result["is_coreference"] is True

    def test_json_with_markdown_fence(self):
        text = '```json\n{"rewritten": "测试", "is_coreference": false, "is_decomposed": false, "sub_queries": [], "reasoning": ""}\n```'
        result = _parse_rewrite_result(text)
        assert result is not None
        assert result["rewritten"] == "测试"

    def test_invalid_json(self):
        text = "这不是JSON"
        result = _parse_rewrite_result(text)
        assert result is None

    def test_empty_text(self):
        assert _parse_rewrite_result("") is None
        assert _parse_rewrite_result(None) is None


class TestRewriteQuery:
    """测试查询改写核心功能。"""

    def test_no_history_skip(self):
        """无历史对话时应跳过改写。"""
        result = rewrite_query("招标项目有哪些？", history_messages=None)
        assert isinstance(result, RewrittenQuery)
        assert result.rewritten == "招标项目有哪些？"
        assert result.is_coreference is False

    def test_empty_history_skip(self):
        """空历史对话列表时应跳过改写。"""
        result = rewrite_query("招标项目有哪些？", history_messages=[])
        assert isinstance(result, RewrittenQuery)
        assert result.is_coreference is False


# ── ReAct Agent 测试 ─────────────────────────────────────────


class TestShouldUseReact:
    """测试复杂度评估。"""

    def test_short_query(self):
        """极短查询不使用 ReAct。"""
        assert should_use_react("能投吗", 0.9) is False
        assert should_use_react("多少", 0.8) is False

    def test_simple_query(self):
        """简单查询不使用 ReAct。"""
        assert should_use_react("这个项目是谁中标的？", 0.9) is False

    def test_complex_comparison(self):
        """比较类查询使用 ReAct。"""
        assert should_use_react("这两家公司的资质有什么区别？", 0.8) is True

    def test_complex_association(self):
        """关联分析类查询使用 ReAct。"""
        assert should_use_react("华为参与过哪些招标项目？", 0.8) is True

    def test_complex_ranking(self):
        """排序+筛选组合使用 ReAct。"""
        assert should_use_react("中标金额最高的三个项目分别是谁中标的？", 0.8) is True

    def test_low_confidence(self):
        """低置信度倾向于使用 ReAct。"""
        assert should_use_react("帮我看看这个", 0.35) is True

    def test_multi_question(self):
        """多问题场景使用 ReAct。"""
        assert should_use_react("这个项目金额多少？哪些公司参与了？", 0.7) is True


class TestBuildToolsSchema:
    """测试工具 Schema 构建。"""

    def test_schema_structure(self):
        schema = _build_tools_schema()
        assert len(schema) == 2

        names = {item["function"]["name"] for item in schema}
        assert "sql_query" in names
        assert "semantic_search" in names

    def test_sql_query_schema(self):
        schema = _build_tools_schema()
        sql_tool = next(item for item in schema if item["function"]["name"] == "sql_query")
        params = sql_tool["function"]["parameters"]
        assert "query_type" in params["properties"]
        assert "domain" in params["properties"]
        assert params["properties"]["query_type"]["enum"] == ["top_by_amount", "count", "list_by_stage", "list"]

    def test_semantic_search_schema(self):
        schema = _build_tools_schema()
        search_tool = next(item for item in schema if item["function"]["name"] == "semantic_search")
        params = search_tool["function"]["parameters"]
        assert "domain" in params["properties"]
        assert "query" in params["properties"]


class TestFormatToolResult:
    """测试工具结果格式化。"""

    def test_sql_success(self):
        result = ToolResult(
            tool_name="sql_query",
            success=True,
            data={"answer": "排名前3的项目...", "row_count": 3, "data": []},
        )
        text = _format_tool_result_for_llm(result)
        assert "[SQL 查询结果]" in text
        assert "共 3 条记录" in text

    def test_sql_failure(self):
        result = ToolResult(
            tool_name="sql_query",
            success=False,
            data=None,
            error="查询超时",
        )
        text = _format_tool_result_for_llm(result)
        assert "执行失败" in text
        assert "查询超时" in text

    def test_search_success(self):
        result = ToolResult(
            tool_name="semantic_search",
            success=True,
            data={
                "total": 2,
                "results": [
                    {"title": "测试项目", "summary": "摘要...", "score": 0.95},
                    {"title": "政策文件", "summary": "内容...", "score": 0.87},
                ],
            },
        )
        text = _format_tool_result_for_llm(result)
        assert "[语义检索结果]" in text
        assert "共找到 2 条" in text
        assert "[文档1]" in text
        assert "[文档2]" in text

    def test_unknown_tool(self):
        result = ToolResult(
            tool_name="unknown_tool",
            success=True,
            data={"key": "value"},
        )
        text = _format_tool_result_for_llm(result)
        assert "key" in text


class TestExecuteTool:
    """测试工具执行（仅测试错误路径，正常路径需要数据库）。"""

    def test_unknown_tool(self):
        result = _execute_tool("nonexistent", {}, None, None, "测试")
        assert result.success is False
        assert "未知工具" in result.error
