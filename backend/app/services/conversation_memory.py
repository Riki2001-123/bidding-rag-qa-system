"""
会话级实体记忆模块（P2）。

维护每个会话的 entity_memory，从 LLM 回答中提取关键实体（项目名、企业名、招标编号等），
在后续追问的 Query Rewrite 阶段注入到 prompt 中，提升指代消解的准确性。

设计原则：
- 轻量级：不引入 NER 模型，用规则 + LLM 提取
- 会话隔离：每个 session_id 独立维护一份实体记忆
- 自动清理：对话超过 20 轮自动截断早期实体
- 失败安全：提取失败不影响主流程
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# LLM 客户端预留（后续可扩展 LLM 实体提取）
# from app.services.llm import close_llm_client, get_llm_client


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class EntityMemory:
    """单个会话的实体记忆。"""

    entities: Dict[str, str] = field(default_factory=dict)   # 实体名 → 类型
    last_queries: List[str] = field(default_factory=list)      # 最近查询历史（用于语义缓存）
    last_query_embedding: Optional[List[float]] = None         # 最近查询的 embedding 向量
    cached_results: Optional[list] = None                      # 最近检索结果缓存（P1 语义门控用）

    def add_entity(self, name: str, entity_type: str) -> None:
        """添加或更新实体。"""
        if name and entity_type:
            self.entities[name] = entity_type

    def get_entities_text(self) -> str:
        """将实体记忆格式化为文本，用于注入 prompt。"""
        if not self.entities:
            return ""
        lines = []
        for name, etype in self.entities.items():
            lines.append(f"- {name}（{etype}）")
        return "已识别的实体：\n" + "\n".join(lines)

    def trim_entities(self, max_entities: int = 30) -> None:
        """限制实体数量，保留最近添加的。"""
        if len(self.entities) > max_entities:
            trimmed = dict(list(self.entities.items())[-max_entities:])
            self.entities.clear()
            self.entities.update(trimmed)

    def record_query(self, query: str) -> None:
        """记录查询历史。"""
        self.last_queries.append(query)
        if len(self.last_queries) > 10:
            self.last_queries = self.last_queries[-10:]


@dataclass
class EntityExtractionResult:
    """实体提取结果。"""
    entities: List[Tuple[str, str]]  # [(实体名, 类型), ...]


# ── 实体类型定义 ──────────────────────────────────────────────

ENTITY_TYPES = ("project", "enterprise", "policy", "amount", "date", "region", "other")

# 招投标领域实体提取正则（规则优先，减少 LLM 调用）
_ENTITY_PATTERNS: List[Tuple[str, str, re.Pattern]] = [
    # 招标编号：如 "ZJZB-2026-001" "（2026）第001号"
    ("bid_number", "policy", re.compile(r"[（(]\s*\d{4}\s*[)）]\s*(?:第|号)\s*\d+\s*号|ZJ\w*-\d{4}-\d+", re.IGNORECASE)),
    # 金额：如 "500万元" "1,234,567元"
    ("amount", "amount", re.compile(r"[\d,]+\.?\d*\s*(?:万|亿)?元")),
    # 统一社会信用代码：18位
    ("credit_code", "enterprise", re.compile(r"\b[0-9A-Z]{18}\b")),
]


# ── 核心服务 ──────────────────────────────────────────────────


class ConversationMemoryManager:
    """
    会话记忆管理器。

    用法：
        manager = ConversationMemoryManager()

        # 改写前获取实体上下文
        entity_text = manager.get_entity_context(session_id)

        # 回答后更新实体记忆
        manager.update_after_answer(session_id, question, answer)
    """

    _MAX_SESSIONS = 500  # 内存中最多保留的会话数
    _MAX_ENTITIES = 30   # 每个会话最多保留的实体数

    def __init__(self) -> None:
        self._memories: Dict[int, EntityMemory] = {}
        self._lock = threading.Lock()
        self._access_order: List[int] = []  # 记录访问顺序，用于 LRU 淘汰

    def _get_memory(self, session_id: Optional[int]) -> EntityMemory:
        """获取或创建会话记忆。"""
        if session_id is None:
            return EntityMemory()  # 无 session 时返回临时空记忆
        with self._lock:
            if session_id not in self._memories:
                # LRU 淘汰：先淘汰再添加，确保不超上限
                while len(self._memories) >= self._MAX_SESSIONS:
                    oldest = self._access_order.pop(0)
                    self._memories.pop(oldest, None)
                self._memories[session_id] = EntityMemory()
                self._access_order.append(session_id)
            else:
                # 更新访问顺序（移到末尾）
                if session_id in self._access_order:
                    self._access_order.remove(session_id)
                self._access_order.append(session_id)
            return self._memories[session_id]

    def get_entity_context(self, session_id: Optional[int]) -> str:
        """
        获取当前会话的实体上下文文本，用于注入 Query Rewrite prompt。
        """
        memory = self._get_memory(session_id)
        return memory.get_entities_text()

    def record_query(self, session_id: Optional[int], query: str) -> None:
        """记录查询到历史中。"""
        memory = self._get_memory(session_id)
        memory.record_query(query)

    def get_memory(self, session_id: Optional[int]) -> EntityMemory:
        """
        获取会话记忆对象（公开接口，供 retrieval_gate 等外部模块使用）。

        注意：返回的是内部可变对象的引用，调用方不应直接修改。
        如需更新 embedding 缓存或检索结果，请使用 update_retrieval_cache() 方法。
        """
        return self._get_memory(session_id)

    def update_retrieval_cache(self, session_id: Optional[int], query_embedding, results: list) -> None:
        """更新检索缓存（embedding + 检索结果），供 retrieval_gate 模块调用。"""
        if session_id is None:
            return
        memory = self._get_memory(session_id)
        memory.last_query_embedding = query_embedding.tolist() if hasattr(query_embedding, 'tolist') else list(query_embedding)
        memory.cached_results = results

    def update_after_answer(self, session_id: Optional[int], question: str, answer: str) -> None:
        """
        回答生成后，从 question + answer 中提取实体并更新记忆。

        采用规则优先策略：先用正则提取结构化实体，再判断是否需要 LLM 提取。
        """
        if session_id is None:
            return

        memory = self._get_memory(session_id)

        # 1) 规则提取（零延迟）
        rule_entities = self._extract_by_rules(question, answer)
        for name, etype in rule_entities:
            memory.add_entity(name, etype)

        # 2) 从问题中提取明显的实体名称（项目名/企业名通常在问题中出现）
        # 不需要 LLM，用简单规则即可
        question_entities = self._extract_from_question(question)
        for name, etype in question_entities:
            memory.add_entity(name, etype)

        memory.trim_entities()

    def _extract_by_rules(self, question: str, answer: str) -> List[Tuple[str, str]]:
        """用正则规则提取实体。"""
        entities = []
        combined = f"{question}\n{answer}"
        for _, etype, pattern in _ENTITY_PATTERNS:
            for match in pattern.finditer(combined):
                entities.append((match.group().strip(), etype))
        return entities

    def _extract_from_question(self, question: str) -> List[Tuple[str, str]]:
        """
        从问题中提取实体名称。

        招投标场景的实体通常以书名号或引号包裹，或出现在特定句式后。
        """
        entities = []

        # 书名号包裹的名称：《xxx》
        book_name_matches = re.findall(r"《([^》]+)》", question)
        for name in book_name_matches:
            entities.append((name, self._guess_entity_type(name)))

        # 引号包裹："xxx" 或 「xxx」
        quote_matches = re.findall(r'[""「]([^"""」]+)[""」]', question)
        for name in quote_matches:
            if len(name) >= 2:
                entities.append((name, self._guess_entity_type(name)))

        return entities

    @staticmethod
    def _guess_entity_type(name: str) -> str:
        """根据实体名称猜测类型。"""
        # 招投标项目常见关键词
        project_keywords = ("采购", "招标", "项目", "工程", "标段")
        enterprise_keywords = ("公司", "集团", "有限", "科技", "建设", "咨询")
        policy_keywords = ("法", "条例", "办法", "规定", "通知", "意见", "细则")

        if any(kw in name for kw in policy_keywords) and len(name) <= 20:
            return "policy"
        if any(kw in name for kw in enterprise_keywords):
            return "enterprise"
        if any(kw in name for kw in project_keywords):
            return "project"
        return "other"

    def clear_session(self, session_id: int) -> None:
        """清除指定会话的记忆。"""
        with self._lock:
            self._memories.pop(session_id, None)
            if session_id in self._access_order:
                self._access_order.remove(session_id)

    def clear_all(self) -> None:
        """清除所有会话记忆。"""
        with self._lock:
            self._memories.clear()
            self._access_order.clear()


# 全局单例
conversation_memory = ConversationMemoryManager()
