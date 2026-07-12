import json
import re
from dataclasses import dataclass
from typing import AsyncIterable, Dict, Iterable, List, Optional, Sequence, Tuple
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import TenderRecord, User
from app.schemas.common import CitationOut
from app.services.conversation_memory import conversation_memory
from app.services.llm import close_llm_client, get_llm_client, llm_service
from app.services.llm_prompts import get_prompt
from app.services.query_rewriter import rewrite_query
from app.services.react_agent import run_react_agent, should_use_react
from app.services.retrieval import apply_permission_filters, get_attachments, search_domain
from app.services.retrieval_gate import check_retrieval_gate, update_retrieval_cache
from app.services.retrieval import RetrievedItem
from app.services.domain_config import VALID_DOMAINS
from app.services.json_utils import extract_json_object
from app.services.retrieval_validator import validate_retrieval_results
from app.services.sql_agent import detect_sql_intent, execute_sql_intent
from app.services.structured_retrieval import retrieve_structured


VALID_INTENTS = ("fact", "filter", "judgment", "association", "aggregate")

DOMAIN_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "policy": (
        "政策",
        "法规",
        "条例",
        "制度",
        "办法",
        "政府采购",
        "采购法",
        "适用范围",
        "生效",
        "失效",
        "废止",
        "条款",
        "文号",
        "资格条件",
    ),
    "tender": (
        "招标",
        "投标",
        "项目",
        "采购项目",
        "标段",
        "中标",
        "中标过",
        "成交",
        "预算金额",
        "中标金额",
        "项目编号",
        "采购公告",
        "代理机构",
        "采购人",
    ),
    "enterprise": (
        "企业",
        "公司",
        "统一社会信用代码",
        "经营范围",
        "工商",
        "关联企业",
        "法定代表人",
        "曾用名",
        "行业",
        "资质",
        "信用代码",
    ),
}

FACT_PATTERNS = ("是什么", "是谁", "多少", "何时", "哪里", "哪家", "有没有", "情况", "介绍")
FILTER_PATTERNS = ("哪些", "筛选", "列表", "清单", "大于", "小于", "高于", "低于", "前十", "范围内")
JUDGMENT_PATTERNS = ("是否", "能否", "可否", "是不是", "算不算", "是否有效", "是否适用", "是否具备")
ASSOCIATION_PATTERNS = ("关联", "关系", "同一", "同名", "曾用名", "中标过", "参与过", "涉及哪些项目", "关联项目")
AGGREGATE_PATTERNS = ("前几", "排名", "前三", "前十", "最多", "最少", "最大", "最小", "最高", "最低", "多少个", "有多少", "几个", "总数", "共计", "合计", "统计", "汇总", "平均", "最贵", "最便宜", "金额最高", "金额最大", "中标金额最高")

DIRECT_TENDER_MAX_AMOUNT_KEYWORDS = ("最大", "最高", "最多")
DIRECT_TENDER_AMOUNT_FIELDS = ("金额", "中标金额", "招标金额", "成交金额", "预算金额")


@dataclass(frozen=True)
class AgentDecision:
    domain: str
    intent: str
    reason: str
    confidence: float
    cross_domain_candidate: bool = False
    candidate_domains: Tuple[str, ...] = ()
    low_confidence: bool = False


@dataclass(frozen=True)
class AgentRunResult:
    domain: str
    answer: str
    citations: List[CitationOut]
    conservative: bool
    decision: AgentDecision


@dataclass(frozen=True)
class AgentEvidence:
    citations: List[CitationOut]
    contexts: List[dict]
    conservative: bool


@dataclass
class _OrchestrationContext:
    """编排准备阶段的结构化结果，供 orchestrate / stream_orchestrate 共用。"""
    decision: AgentDecision
    effective_question: str
    agent: "BaseBusinessAgent"
    entity_context: str
    gate_result: object  # RetrievalGateResult


class JudgeAgent:
    def judge(
        self,
        question: str,
        preferred_domain: Optional[str],
        user_role: str,
        history_messages: Optional[Sequence[dict]] = None,
    ) -> AgentDecision:
        rule_decision = self._rule_based_decision(question, preferred_domain)

        # ── P0-1: 高置信度跳过 LLM ──
        # 规则引擎置信度 >= 0.8 且非低置信度时，直接使用规则结果，避免额外 LLM 调用（节省 1~3 秒）
        if rule_decision.confidence >= 0.8 and not rule_decision.low_confidence:
            return rule_decision

        llm_decision = self._judge_with_llm(
            question=question,
            preferred_domain=preferred_domain,
            user_role=user_role,
            history_messages=history_messages or [],
            rule_decision=rule_decision,
        )
        if not llm_decision:
            return rule_decision

        llm_domain = llm_decision.get("primary_domain") or llm_decision.get("domain")
        llm_confidence = _normalize_confidence(llm_decision.get("confidence"), default=rule_decision.confidence)
        llm_reason = str(llm_decision.get("reason") or rule_decision.reason).strip()
        llm_intent = str(llm_decision.get("intent") or rule_decision.intent).strip().lower()
        if llm_intent not in VALID_INTENTS:
            llm_intent = rule_decision.intent

        cross_domain_candidate = bool(llm_decision.get("cross_domain_candidate")) or rule_decision.cross_domain_candidate
        candidate_domains = _merge_candidate_domains(
            rule_decision.candidate_domains,
            llm_decision.get("candidate_domains") or [],
        )

        if llm_domain in VALID_DOMAINS and llm_confidence >= 0.55:
            return AgentDecision(
                domain=llm_domain,
                intent=llm_intent,
                reason=llm_reason,
                confidence=llm_confidence,
                cross_domain_candidate=cross_domain_candidate,
                candidate_domains=candidate_domains or rule_decision.candidate_domains,
                low_confidence=False,
            )

        fallback_domain = preferred_domain if preferred_domain in VALID_DOMAINS else rule_decision.domain
        fallback_reason = llm_reason or "Judge Agent 置信度不足，回退到保守路由策略。"
        return AgentDecision(
            domain=fallback_domain,
            intent=rule_decision.intent,
            reason=fallback_reason,
            confidence=min(llm_confidence, rule_decision.confidence),
            cross_domain_candidate=cross_domain_candidate,
            candidate_domains=candidate_domains or rule_decision.candidate_domains,
            low_confidence=True,
        )

    def _rule_based_decision(self, question: str, preferred_domain: Optional[str]) -> AgentDecision:
        normalized_question = (question or "").strip()
        domain_scores = {
            domain: sum(1 for keyword in keywords if keyword in normalized_question)
            for domain, keywords in DOMAIN_KEYWORDS.items()
        }

        matched_domains = tuple(
            domain for domain, score in sorted(domain_scores.items(), key=lambda item: item[1], reverse=True) if score > 0
        )
        intent = self._detect_intent(normalized_question)

        if matched_domains:
            primary_domain = matched_domains[0]
            reason = f"规则命中 {primary_domain} 领域关键词 {domain_scores[primary_domain]} 个。"
            confidence = min(0.5 + 0.15 * domain_scores[primary_domain], 0.9)
            cross_domain_candidate = len(matched_domains) > 1
            return AgentDecision(
                domain=primary_domain,
                intent=intent,
                reason=reason,
                confidence=confidence,
                cross_domain_candidate=cross_domain_candidate,
                candidate_domains=matched_domains,
                low_confidence=False,
            )

        fallback_domain = preferred_domain if preferred_domain in VALID_DOMAINS else "tender"
        fallback_reason = "未命中明显领域关键词，按保守策略回退到显式 domain 或默认 tender。"
        return AgentDecision(
            domain=fallback_domain,
            intent=intent,
            reason=fallback_reason,
            confidence=0.35,
            cross_domain_candidate=False,
            candidate_domains=(fallback_domain,),
            low_confidence=True,
        )

    def _judge_with_llm(
        self,
        question: str,
        preferred_domain: Optional[str],
        user_role: str,
        history_messages: Sequence[dict],
        rule_decision: AgentDecision,
    ) -> Optional[dict]:
        llm = get_llm_client()
        if llm is None:
            return None

        prompt = get_prompt("judge")
        history_summary = "\n".join(
            f"- {item['role']}: {item['content']}" for item in history_messages[-4:] if item.get("content")
        )
        hints = {
            "preferred_domain": preferred_domain,
            "rule_domain": rule_decision.domain,
            "rule_intent": rule_decision.intent,
            "rule_confidence": rule_decision.confidence,
            "candidate_domains": list(rule_decision.candidate_domains),
            "history_summary": history_summary,
        }

        try:
            chain = prompt | llm
            result = chain.invoke(
                {
                    "domain": preferred_domain or rule_decision.domain,
                    "user_role": user_role,
                    "question": question,
                    "evidence": json.dumps(hints, ensure_ascii=False),
                }
            )
        except Exception as exc:
            print(f"[Judge] LLM route failed, fallback to rules: {exc}")
            return None
        finally:
            close_llm_client(llm)

        return extract_json_object(getattr(result, "content", "") or "")

    @staticmethod
    def _detect_intent(question: str) -> str:
        if any(pattern in question for pattern in ASSOCIATION_PATTERNS):
            return "association"
        if any(pattern in question for pattern in AGGREGATE_PATTERNS):
            return "aggregate"
        if any(pattern in question for pattern in JUDGMENT_PATTERNS):
            return "judgment"
        if any(pattern in question for pattern in FILTER_PATTERNS):
            return "filter"
        if any(pattern in question for pattern in FACT_PATTERNS):
            return "fact"
        return "fact"


class BaseBusinessAgent:
    domain = ""
    label = ""

    def can_handle(self, decision: AgentDecision) -> bool:
        return decision.domain == self.domain

    def run(
        self,
        db: Session,
        user: User,
        question: str,
        top_k: int,
        history_messages: Sequence[dict],
        decision: AgentDecision,
        entity_context: str = "",
    ) -> AgentRunResult:
        # 1) Try SQL Agent for structured queries (aggregate, count, ranking)
        sql_result = self._try_sql_query(db, user, question)
        if sql_result is not None:
            answer = self._finalize_answer(sql_result["answer"], decision)
            return AgentRunResult(
                domain=self.domain,
                answer=answer,
                citations=sql_result["citations"],
                conservative=False,
                decision=decision,
            )

        # 2) Try structured metadata retrieval for entity-style tender/enterprise questions
        structured_result = self._try_structured_retrieval(db, user, question, top_k)
        if structured_result is not None:
            answer = self._finalize_answer(structured_result["answer"], decision)
            return AgentRunResult(
                domain=self.domain,
                answer=answer,
                citations=structured_result["citations"],
                conservative=False,
                decision=decision,
            )

        # 3) Try direct answer
        direct_result = self.try_answer_directly(db, user, question)
        if direct_result is not None:
            answer = self._finalize_answer(direct_result["answer"], decision)
            return AgentRunResult(
                domain=self.domain,
                answer=answer,
                citations=direct_result["citations"],
                conservative=direct_result["conservative"],
                decision=decision,
            )

        # 4) Normal RAG retrieval + LLM
        evidence = self.collect_evidence(db=db, user=user, question=question, top_k=top_k)
        answer = self.answer(question, user.role, evidence.contexts, history_messages, entity_context)
        answer = self._finalize_answer(answer, decision)
        return AgentRunResult(
            domain=self.domain,
            answer=answer,
            citations=evidence.citations,
            conservative=evidence.conservative,
            decision=decision,
        )

    def _try_sql_query(self, db: Session, user: User, question: str) -> Optional[dict]:
        """Try to detect and execute a structured SQL query."""
        sql_intent = detect_sql_intent(question, self.domain)
        if sql_intent is None:
            return None

        result = execute_sql_intent(db, user, sql_intent, question)
        if result is None or not result.success:
            return None
        if not result.data and not result.citations:
            return None

        citations = [
            CitationOut(**c) for c in result.citations
        ]

        return {"answer": result.answer_text, "citations": citations}

    def _try_structured_retrieval(self, db: Session, user: User, question: str, top_k: int) -> Optional[dict]:
        if self.domain not in ("tender", "enterprise"):
            return None
        result = retrieve_structured(db=db, user=user, domain=self.domain, question=question, top_k=top_k)
        if not result.success:
            return None
        return {"answer": result.answer_text, "citations": result.citations}

    def retrieve(self, db: Session, user: User, question: str, top_k: int):
        return search_domain(db=db, domain=self.domain, user=user, q=question, top_k=top_k)

    def answer(self, question: str, user_role: str, contexts: Iterable[dict], history_messages: Sequence[dict], entity_context: str = "") -> str:
        return llm_service.generate_answer(question, self.domain, user_role, contexts, list(history_messages), entity_context)

    def try_answer_directly(self, db: Session, user: User, question: str):
        return None

    def collect_evidence(self, db: Session, user: User, question: str, top_k: int) -> AgentEvidence:
        results = self.retrieve(db=db, user=user, question=question, top_k=top_k)
        # P0-1: 检索质量验证
        validated, need_supplement, stats = validate_retrieval_results(results, question)
        if need_supplement and top_k is not None:
            # 补充检索：扩大 top_k 拉取更多候选
            try:
                extra_top_k = top_k + 5
                extra_results = self.retrieve(db=db, user=user, question=question, top_k=extra_top_k)
                if extra_results:
                    validated, _, _ = validate_retrieval_results(extra_results, question)
                    print(f"[RetrievalValidator] 补充检索完成: {len(extra_results)} -> {len(validated)}", flush=True)
            except Exception as exc:
                print(f"[RetrievalValidator] 补充检索失败（使用原始结果）: {exc}", flush=True)
        return self._build_evidence(db=db, user=user, results=validated)

    @staticmethod
    def _build_evidence(db: Session, user: User, results) -> AgentEvidence:
        citations = []
        contexts = []
        for item in results:
            attachments = get_attachments(db, item.domain, item.record_id, user)
            citation = CitationOut(
                domain=item.domain,
                record_id=item.record_id,
                title=item.title,
                score=item.score,
                source_fields=item.source_fields,
                key_fields=item.key_fields,
                attachments=attachments,
            )
            citations.append(citation)
            contexts.append(
                {
                    "domain": item.domain,
                    "title": item.title,
                    "summary": item.summary,
                    "key_fields": item.key_fields,
                    "attachments": attachments,
                }
            )
        return AgentEvidence(citations=citations, contexts=contexts, conservative=not results)

    @staticmethod
    def _finalize_answer(answer: str, decision: AgentDecision) -> str:
        if not decision.cross_domain_candidate:
            return answer

        related_domains = [domain for domain in decision.candidate_domains if domain != decision.domain]
        if not related_domains:
            return answer

        note = (
            f"补充说明：该问题带有跨域特征，本次先按 {decision.domain} 域给出结论；"
            f"如需更完整判断，建议再结合 {'、'.join(related_domains)} 域证据继续核实。"
        )
        return f"{answer}\n\n{note}"


class PolicyAgent(BaseBusinessAgent):
    domain = "policy"
    label = "Policy Agent"


class TenderAgent(BaseBusinessAgent):
    domain = "tender"
    label = "Tender Agent"

    def try_answer_directly(self, db: Session, user: User, question: str):
        normalized_question = (question or "").strip()
        if not normalized_question or not _looks_like_max_amount_question(normalized_question):
            return None

        stmt = select(TenderRecord).where(TenderRecord.bid_amount.is_not(None))
        stmt = apply_permission_filters(stmt, TenderRecord, db, user)
        stmt = stmt.order_by(TenderRecord.bid_amount.desc(), TenderRecord.id.desc())
        record = db.scalars(stmt.limit(1)).first()
        if not record:
            return {
                "answer": (
                    "直接结论：当前可检索到的招标记录中，暂无可用于统计最大金额的有效数据。\n\n"
                    "依据说明：系统未检索到带有有效金额字段的招标或中标记录。\n\n"
                    "补充说明：如需继续核实，建议缩小项目范围或明确时间、地区、采购人等筛选条件。"
                ),
                "citations": [],
                "conservative": True,
            }

        attachments = get_attachments(db, "tender", record.id, user)
        citation = CitationOut(
            domain="tender",
            record_id=record.id,
            title=record.title or record.project_name,
            score=1.0,
            source_fields=["structured", "bid_amount"],
            key_fields={
                "project_name": record.project_name,
                "tenderer": record.tenderer,
                "stage": record.stage,
                "region": record.region,
                "bid_amount": record.bid_amount,
                "source_url": record.source_url,
            },
            attachments=attachments,
        )

        title = record.title or record.project_name or f"招标记录 {record.id}"
        amount_text = _format_amount(record.bid_amount)
        parts = [f"直接结论：当前可检索到的招标记录里，最大金额为 {amount_text} 元，对应项目是《{title}》。"]
        basis = [f"依据说明：该记录的金额字段为 {amount_text} 元。"]
        if record.tenderer:
            basis.append(f"采购人或招标人为 {record.tenderer}。")
        if record.publish_date:
            basis.append(f"发布时间为 {record.publish_date}。")
        parts.append(" ".join(basis))
        parts.append("补充说明：以上结论基于当前可见且带有金额字段的招标记录，详情见引用记录。")
        return {"answer": "\n\n".join(parts), "citations": [citation], "conservative": False}


class EnterpriseAgent(BaseBusinessAgent):
    domain = "enterprise"
    label = "Enterprise Agent"


class ChatOrchestrator:
    def __init__(self, judge_agent: Optional[JudgeAgent] = None, agents: Optional[Dict[str, BaseBusinessAgent]] = None):
        self.judge_agent = judge_agent or JudgeAgent()
        self.agents = agents or {
            "policy": PolicyAgent(),
            "tender": TenderAgent(),
            "enterprise": EnterpriseAgent(),
        }

    def _prepare_orchestration(
        self,
        db: Session,
        user: User,
        question: str,
        preferred_domain: Optional[str],
        top_k: int,
        history_messages: Sequence[dict],
        session_id: Optional[int],
    ) -> _OrchestrationContext:
        """公共准备阶段：Judge + Rewrite + Gate + 日志。供 orchestrate / stream_orchestrate 共用。"""
        from concurrent.futures import ThreadPoolExecutor

        entity_context = conversation_memory.get_entity_context(session_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            judge_future = pool.submit(
                self.judge_agent.judge,
                question, preferred_domain, user.role, history_messages,
            )
            rewrite_future = pool.submit(
                rewrite_query, question, history_messages, entity_context,
            )
            decision = judge_future.result()
            rewritten = rewrite_future.result()

        agent = self.agents[decision.domain]
        self._log_decision(question, decision)
        effective_question = rewritten.rewritten
        if rewritten.is_coreference or rewritten.is_decomposed:
            self._log_rewrite(question, rewritten)

        gate_result = self._check_and_apply_gate(
            db=db, user=user, session_id=session_id,
            effective_question=effective_question, top_k=top_k,
        )

        return _OrchestrationContext(
            decision=decision,
            effective_question=effective_question,
            agent=agent,
            entity_context=entity_context,
            gate_result=gate_result,
        )

    def _route_and_retrieve(
        self,
        db: Session,
        user: User,
        ctx: _OrchestrationContext,
        top_k: int,
        history_messages: Sequence[dict],
        session_id: Optional[int],
    ) -> Tuple[Optional[str], List[dict], List[CitationOut], bool, Optional[AgentEvidence]]:
        """公共路由+检索阶段：ReAct → 跨域 → 门控复用 → 标准RAG。

        Returns:
            (react_answer, contexts, citations, conservative, retrieval_evidence)
            - react_answer: ReAct 成功时返回最终答案（调用方无需再走 LLM），否则 None
            - contexts/citations/conservative: 非 ReAct 路径的证据
            - retrieval_evidence: 用于缓存更新的原始证据（跨域/门控复用时为 None）
        """
        agent = ctx.agent
        effective_question = ctx.effective_question
        decision = ctx.decision
        entity_context = ctx.entity_context
        gate_result = ctx.gate_result

        # ── ReAct Agent 自适应路由 ──
        if not self._should_run_cross_domain(decision) and should_use_react(effective_question, decision.confidence):
            react_result = run_react_agent(
                db=db,
                user=user,
                question=effective_question,
                domain=decision.domain,
                history_messages=list(history_messages) if history_messages else None,
            )
            if react_result.used_react and react_result.answer:
                print(f"[ReAct] used react=true, tool_calls={react_result.tool_calls_count}, steps={len(react_result.steps)}")
                conversation_memory.update_after_answer(session_id, effective_question, react_result.answer)
                answer = agent._finalize_answer(react_result.answer, decision)
                return answer, [], [], False, None

        # ── 跨域检索 ──
        if self._should_run_cross_domain(decision):
            direct_result = agent.try_answer_directly(db, user, effective_question)
            if direct_result is not None:
                conversation_memory.update_after_answer(session_id, effective_question, direct_result["answer"])
                answer = agent._finalize_answer(direct_result["answer"], decision)
                return answer, [], direct_result["citations"], direct_result["conservative"], None

            cross_result = self._run_cross_domain(
                db=db, user=user, question=effective_question,
                top_k=top_k, history_messages=history_messages,
                decision=decision, entity_context=entity_context,
            )
            conversation_memory.update_after_answer(session_id, effective_question, cross_result.answer)
            # 跨域结果已包含最终 answer，调用方直接使用
            return cross_result.answer, [], cross_result.citations, cross_result.conservative, None

        # ── 门控复用 ──
        if gate_result.action == "reuse" and gate_result.cached_results is not None:
            cached_evidence = agent._build_evidence(db=db, user=user, results=gate_result.cached_results)
            print(f"[RetrievalGate] 复用上轮结果，跳过检索，节省 ~1s", flush=True)
            return None, cached_evidence.contexts, cached_evidence.citations, cached_evidence.conservative, None

        # ── 标准 RAG ──
        evidence = agent.collect_evidence(db=db, user=user, question=effective_question, top_k=top_k)
        return None, evidence.contexts, evidence.citations, evidence.conservative, evidence

    def orchestrate(
        self,
        db: Session,
        user: User,
        question: str,
        preferred_domain: Optional[str],
        top_k: int,
        history_messages: Sequence[dict],
        session_id: Optional[int] = None,
    ) -> AgentRunResult:
        ctx = self._prepare_orchestration(
            db, user, question, preferred_domain, top_k, history_messages, session_id,
        )
        agent = ctx.agent

        react_answer, contexts, citations, conservative, retrieval_evidence = self._route_and_retrieve(
            db, user, ctx, top_k, history_messages, session_id,
        )

        # ReAct / 跨域 / direct 路径已有最终答案
        if react_answer is not None:
            return AgentRunResult(
                domain=ctx.decision.domain,
                answer=react_answer,
                citations=citations,
                conservative=conservative,
                decision=ctx.decision,
            )

        # 门控复用 / 标准 RAG：需要 LLM 生成
        answer = agent.answer(ctx.effective_question, user.role, contexts, history_messages, ctx.entity_context)
        answer = agent._finalize_answer(answer, ctx.decision)
        conversation_memory.update_after_answer(session_id, question, answer)

        if retrieval_evidence is not None:
            self._update_retrieval_cache(
                session_id=session_id,
                effective_question=ctx.effective_question,
                agent=agent,
                db=db,
                user=user,
                top_k=top_k,
                cached_results=_evidence_items_from_evidence(retrieval_evidence),
            )

        return AgentRunResult(
            domain=ctx.decision.domain,
            answer=answer,
            citations=citations,
            conservative=conservative,
            decision=ctx.decision,
        )

    async def stream_orchestrate(
        self,
        db: Session,
        user: User,
        question: str,
        preferred_domain: Optional[str],
        top_k: int,
        history_messages: Sequence[dict],
        session_id: Optional[int] = None,
    ):
        """
        流式编排：复用 _prepare_orchestration + _route_and_retrieve，
        只在生成阶段走 SSE 流式输出。
        yield 格式: {"type": "meta", ...} 或 {"type": "chunk", "content": "..."} 或 {"type": "done", ...}
        """
        ctx = self._prepare_orchestration(
            db, user, question, preferred_domain, top_k, history_messages, session_id,
        )

        react_answer, contexts, citations, conservative, retrieval_evidence = self._route_and_retrieve(
            db, user, ctx, top_k, history_messages, session_id,
        )

        # ── ReAct / 跨域 / direct：已有最终答案，模拟流式输出 ──
        if react_answer is not None:
            meta = {
                "type": "meta",
                "domain": ctx.decision.domain,
                "conservative": conservative,
                "citations": [
                    {"domain": c.domain, "record_id": c.record_id, "title": c.title,
                     "score": c.score, "source_fields": c.source_fields, "key_fields": c.key_fields}
                    for c in citations
                ],
            }
            yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
            chunk_size = 20
            for i in range(0, len(react_answer), chunk_size):
                yield f"data: {json.dumps({'type': 'chunk', 'content': react_answer[i:i + chunk_size]}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'answer': react_answer}, ensure_ascii=False)}\n\n"
            return

        # ── 门控复用 / 标准 RAG：流式 LLM 生成 ──
        meta = {
            "type": "meta",
            "domain": ctx.decision.domain,
            "conservative": conservative,
            "citations": [
                {"domain": c.domain, "record_id": c.record_id, "title": c.title,
                 "score": c.score, "source_fields": c.source_fields, "key_fields": c.key_fields}
                for c in citations
            ],
        }
        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"

        full_answer = ""
        async for chunk_text in llm_service.stream_answer(
            question=ctx.effective_question,
            domain=ctx.decision.domain,
            user_role=user.role,
            contexts=contexts,
            history_messages=list(history_messages) if history_messages else None,
            entity_context=ctx.entity_context,
        ):
            full_answer += chunk_text
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk_text}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'answer': full_answer}, ensure_ascii=False)}\n\n"

        # P1: 更新检索缓存 + P2: 更新实体记忆
        if retrieval_evidence is not None:
            self._update_retrieval_cache(
                session_id=session_id,
                effective_question=ctx.effective_question,
                agent=ctx.agent,
                db=db,
                user=user,
                top_k=top_k,
                cached_results=_evidence_items_from_evidence(retrieval_evidence),
            )
        conversation_memory.update_after_answer(session_id, question, full_answer)

    def _run_cross_domain(
        self,
        db: Session,
        user: User,
        question: str,
        top_k: int,
        history_messages: Sequence[dict],
        decision: AgentDecision,
        entity_context: str = "",
    ) -> AgentRunResult:
        ordered_domains = [decision.domain] + [
            domain
            for domain in decision.candidate_domains
            if domain != decision.domain and domain in self.agents
        ]

        # P0-2: 跨域并行检索（Specialist Panel 模式）
        from concurrent.futures import ThreadPoolExecutor, as_completed

        combined_citations: List[CitationOut] = []
        combined_contexts: List[dict] = []
        all_conservative = True
        with ThreadPoolExecutor(max_workers=len(ordered_domains)) as pool:
            future_to_domain = {
                pool.submit(
                    self.agents[domain].collect_evidence,
                    db, user, question, top_k,
                ): domain
                for domain in ordered_domains
            }
            for future in as_completed(future_to_domain):
                evidence = future.result()
                all_conservative = all_conservative and evidence.conservative
                combined_citations.extend(evidence.citations)
                for context in evidence.contexts:
                    combined_contexts.append(
                        {
                            **context,
                            "title": f"[{context['domain']}] {context['title']}",
                            "summary": f"领域: {context['domain']}\n{context['summary']}",
                        }
                    )

        answer = self.agents[decision.domain].answer(
            question,
            user.role,
            combined_contexts,
            history_messages,
            entity_context,
        )
        answer = self.agents[decision.domain]._finalize_answer(answer, decision)
        return AgentRunResult(
            domain=decision.domain,
            answer=answer,
            citations=combined_citations,
            conservative=all_conservative,
            decision=decision,
        )

    @staticmethod
    def _should_run_cross_domain(decision: AgentDecision) -> bool:
        return decision.cross_domain_candidate and len(decision.candidate_domains) > 1

    def _check_and_apply_gate(self, db, user, session_id, effective_question, top_k):
        """P1: 语义相似度门控 — 判断是否可以跳过或简化检索。"""
        try:
            from app.services.embeddings import embedding_service
            current_embedding = embedding_service.embed_query(effective_question)
            return check_retrieval_gate(session_id, effective_question, current_embedding)
        except Exception as exc:
            print(f"[RetrievalGate] 门控检查失败，执行全新检索: {exc}", flush=True)
            from app.services.retrieval_gate import RetrievalGateResult
            return RetrievalGateResult(
                action="full_search", similarity=0.0, reason=f"门控异常: {exc}",
            )

    @staticmethod
    def _update_retrieval_cache(session_id, effective_question, agent, db, user, top_k, cached_results=None):
        """P1: 用已有的检索结果更新缓存（避免重复检索）。"""
        if session_id is None:
            return
        try:
            from app.services.embeddings import embedding_service
            query_embedding = embedding_service.embed_query(effective_question)
            if cached_results is None:
                # fallback：执行一次检索（仅在调用方未传缓存时）
                cached_results = agent.retrieve(db=db, user=user, question=effective_question, top_k=top_k)
            update_retrieval_cache(session_id, effective_question, query_embedding, cached_results)
        except Exception as exc:
            print(f"[RetrievalGate] 缓存更新失败（不影响主流程）: {exc}", flush=True)

    @staticmethod
    def _log_decision(question: str, decision: AgentDecision) -> None:
        print(
            "[Judge] "
            f"domain={decision.domain} "
            f"intent={decision.intent} "
            f"confidence={decision.confidence:.2f} "
            f"cross_domain={decision.cross_domain_candidate} "
            f"low_confidence={decision.low_confidence} "
            f"candidates={list(decision.candidate_domains)} "
            f"reason={decision.reason} "
            f"question={question}"
        )

    @staticmethod
    def _log_rewrite(original: str, rewritten) -> None:
        print(
            "[QueryRewrite] "
            f"original={original} "
            f"rewritten={rewritten.rewritten} "
            f"coreference={rewritten.is_coreference} "
            f"decomposed={rewritten.is_decomposed} "
            f"sub_queries={rewritten.sub_queries} "
            f"reasoning={rewritten.reasoning}"
        )


def _looks_like_max_amount_question(question: str) -> bool:
    has_amount = any(keyword in question for keyword in DIRECT_TENDER_AMOUNT_FIELDS)
    has_max = any(keyword in question for keyword in DIRECT_TENDER_MAX_AMOUNT_KEYWORDS)
    return has_amount and has_max


def _format_amount(value: Optional[float]) -> str:
    if value is None:
        return "0"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _normalize_confidence(value, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))


def _merge_candidate_domains(*candidate_groups) -> Tuple[str, ...]:
    merged = []
    for group in candidate_groups:
        for domain in group:
            if domain in VALID_DOMAINS and domain not in merged:
                merged.append(domain)
    return tuple(merged)


def _evidence_items_from_evidence(evidence: AgentEvidence) -> list:
    """从 AgentEvidence 提取 RetrievedItem 列表，用于检索缓存。"""
    items = []
    for i, ctx in enumerate(evidence.contexts):
        citation = evidence.citations[i] if i < len(evidence.citations) else None
        items.append(RetrievedItem(
            domain=ctx.get("domain", citation.domain if citation else ""),
            record_id=citation.record_id if citation else 0,
            title=ctx.get("title", citation.title if citation else ""),
            score=citation.score if citation else 0.0,
            summary=ctx.get("summary", ""),
            publish_date=None,
            key_fields=ctx.get("key_fields", {}),
            source_fields=citation.source_fields if citation else [],
        ))
    return items
