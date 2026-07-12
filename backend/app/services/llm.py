import json
import sys
from typing import AsyncIterable, Iterable, List, Optional

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.config import settings
from app.services.llm_prompts import build_history_system_prompt, get_prompt

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None

try:
    import openai
except Exception:  # pragma: no cover
    openai = None


def _build_http_client() -> httpx.Client:
    # Build a fresh HTTP/1.1 client per request to avoid unstable keep-alive/TLS
    # reuse on some OpenAI-compatible gateways.
    timeout = httpx.Timeout(settings.openai_timeout_seconds, connect=min(settings.openai_timeout_seconds, 20))
    limits = httpx.Limits(max_keepalive_connections=0, max_connections=1)
    return httpx.Client(http2=False, timeout=timeout, limits=limits)


def get_llm_client():
    """Create a fresh ChatOpenAI client for each call."""
    if ChatOpenAI is None:
        print("[LLM] ChatOpenAI 导入失败，请检查 langchain-openai 是否已安装", flush=True)
        return None
    if not settings.openai_api_key:
        print("[LLM] openai_api_key 为空，LLM 未启用", flush=True)
        return None
    if not settings.openai_base_url:
        print("[LLM] openai_base_url 为空，LLM 未启用", flush=True)
        return None

    try:
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=0.1,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
            http_client=_build_http_client(),
        )
    except Exception as exc:
        print(f"[LLM] 初始化失败，未启用远程模型 | detail={exc!r}", flush=True)
        return None


def close_llm_client(llm) -> None:
    http_client = getattr(llm, "http_client", None)
    if http_client is None:
        return
    try:
        http_client.close()
    except Exception:
        pass


def reset_llm_client() -> None:
    # Kept for compatibility with existing imports/tests. We no longer cache.
    return None


class LLMService:
    def generate_answer(
        self,
        question: str,
        domain: str,
        user_role: str,
        contexts: Iterable[dict],
        history_messages: Optional[list] = None,
        entity_context: str = "",
    ) -> str:
        contexts = list(contexts)
        for attempt in range(2):
            llm = get_llm_client()
            if llm is None:
                print(f"[LLM] get_llm_client() 返回 None，跳过 LLM 调用，将返回 fallback (contexts={len(contexts)})", flush=True)
                break
            try:
                return self._call_langchain(llm, question, domain, user_role, contexts, history_messages, entity_context)
            except Exception as exc:
                retryable = self._is_retryable_llm_error(exc)
                self._log_llm_error(
                    exc=exc,
                    question=question,
                    domain=domain,
                    context_count=len(contexts),
                    history_count=len(history_messages or []),
                    attempt=attempt + 1,
                    will_retry=retryable and attempt == 0,
                )
                if not (retryable and attempt == 0):
                    break
            finally:
                close_llm_client(llm)
        return self._fallback_answer(user_role, contexts)

    async def stream_answer(
        self,
        question: str,
        domain: str,
        user_role: str,
        contexts: Iterable[dict],
        history_messages: Optional[list] = None,
        entity_context: str = "",
    ) -> AsyncIterable[str]:
        """流式生成回答，逐 chunk yield 文本片段。失败时返回 fallback 全文。"""
        contexts = list(contexts)
        llm = get_llm_client()
        if llm is None:
            print(f"[LLM] stream_answer: get_llm_client() 返回 None，返回 fallback (contexts={len(contexts)})", flush=True)
            yield self._fallback_answer(user_role, contexts)
            return

        try:
            print(f"[LLM] stream_answer 开始调用 LLM | domain={domain} | contexts={len(contexts)} | question={question[:80]}", flush=True)
            evidence = self._build_evidence(contexts)
            messages = self._build_messages(
                llm, question, domain, user_role, evidence, history_messages, entity_context
            )
            async for chunk in llm.astream(messages):
                if chunk and chunk.content:
                    yield chunk.content
            print("[LLM] stream_answer 完成", flush=True)
            return
        except Exception as exc:
            self._log_llm_error(
                exc=exc,
                question=question,
                domain=domain,
                context_count=len(contexts),
                history_count=len(history_messages or []),
                attempt=1,
                will_retry=False,
            )
            # 流式中途失败，发送 fallback
            yield self._fallback_answer(user_role, contexts)
        finally:
            close_llm_client(llm)

    def _build_evidence(self, contexts: List[dict]) -> str:
        return "\n\n".join(
            [
                f"[证据{i + 1}] 标题: {ctx['title']}\n"
                f"摘要: {ctx['summary']}\n"
                f"关键字段: {json.dumps(ctx['key_fields'], ensure_ascii=False)}"
                for i, ctx in enumerate(contexts)
            ]
        )

    def _build_messages(
        self,
        llm,
        question: str,
        domain: str,
        user_role: str,
        evidence: str,
        history_messages: Optional[list] = None,
        entity_context: str = "",
    ) -> list:
        # P0-3: 将实体记忆注入 system prompt
        system_content = build_history_system_prompt(domain, user_role)
        if entity_context:
            system_content += f"\n\n{entity_context}"

        if history_messages:
            messages = [SystemMessage(content=system_content)]
            recent_history = history_messages[-6:] if len(history_messages) > 6 else history_messages
            for msg in recent_history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))
            messages.append(HumanMessage(content=f"问题：{question}\n\n证据：\n{evidence}"))
            return messages

        prompt = get_prompt(domain)
        chain = prompt | llm
        # 对 chain 模式，直接返回 messages 供 astream 使用
        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=f"问题：{question}\n\n证据：\n{evidence}"),
        ]
        return messages

    def _call_langchain(
        self,
        llm,
        question: str,
        domain: str,
        user_role: str,
        contexts: List[dict],
        history_messages: Optional[list] = None,
        entity_context: str = "",
    ) -> str:
        evidence = self._build_evidence(contexts)

        # P0-3: 将实体记忆注入 system prompt
        system_content = build_history_system_prompt(domain, user_role)
        if entity_context:
            system_content += f"\n\n{entity_context}"

        if history_messages:
            messages = [SystemMessage(content=system_content)]
            recent_history = history_messages[-6:] if len(history_messages) > 6 else history_messages
            for msg in recent_history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))
            messages.append(HumanMessage(content=f"问题：{question}\n\n证据：\n{evidence}"))
            result = llm.invoke(messages)
            return result.content.strip()

        prompt = get_prompt(domain)
        chain = prompt | llm
        result = chain.invoke(
            {
                "domain": domain,
                "user_role": user_role,
                "question": question,
                "evidence": evidence,
            }
        )
        return result.content.strip()

    def _fallback_answer(self, user_role: str, contexts: List[dict]) -> str:
        if not contexts:
            return (
                "直接结论：当前未检索到足够证据，暂时无法确认该问题。\n\n"
                "依据说明：系统没有找到与问题直接相关的结构化记录或文本证据。\n\n"
                "补充说明：请补充更准确的关键词、项目名称、企业名称或法规名称后再试。"
            )

        conclusion = "直接结论：根据当前可见数据，能够确认以下信息。"
        if user_role == "supplier":
            conclusion = "直接结论：根据当前授权范围内可见的数据，能够确认以下信息。"

        evidence_lines = []
        for index, ctx in enumerate(contexts[:3], start=1):
            evidence_lines.append(f"{index}. {ctx['title']}：{ctx['summary']}")
            key_text = "；".join(
                [f"{key}: {value}" for key, value in ctx["key_fields"].items() if value not in ("", None)]
            )
            if key_text:
                evidence_lines.append(f"   关键字段：{key_text}")

        return "\n\n".join(
            [
                conclusion,
                "依据说明：\n" + "\n".join(evidence_lines),
                "补充说明：以上结论基于检索到的结构化记录与文本字段整理，建议结合引用记录进一步核实。",
            ]
        )

    def _log_llm_error(
        self,
        exc: Exception,
        question: str,
        domain: str,
        context_count: int,
        history_count: int,
        attempt: int,
        will_retry: bool,
    ) -> None:
        detail = self._format_llm_exception(exc)
        question_preview = (question or "").replace("\n", " ").strip()[:120]
        print(
            "[LLM] 调用失败，回退到 fallback | "
            f"type={type(exc).__name__} | "
            f"attempt={attempt} | "
            f"will_retry={will_retry} | "
            f"detail={detail} | "
            f"domain={domain} | "
            f"model={settings.openai_model} | "
            f"base_url={settings.openai_base_url} | "
            f"contexts={context_count} | "
            f"history={history_count} | "
            f"question={question_preview}",
            flush=True,
        )

    @staticmethod
    def _is_retryable_llm_error(exc: Exception) -> bool:
        if openai is None:
            return False
        return isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError))

    @staticmethod
    def _format_llm_exception(exc: Exception) -> str:
        if openai is not None:
            if isinstance(exc, openai.APITimeoutError):
                return LLMService._compose_exception_detail(exc, "LLM request timed out")
            if isinstance(exc, openai.APIConnectionError):
                return LLMService._compose_exception_detail(exc, "LLM connection failed")
            if isinstance(exc, openai.RateLimitError):
                return LLMService._compose_exception_detail(exc, "LLM rate limited")
            if isinstance(exc, openai.AuthenticationError):
                return LLMService._compose_exception_detail(exc, "LLM authentication failed")
            if isinstance(exc, openai.PermissionDeniedError):
                return LLMService._compose_exception_detail(exc, "LLM permission denied")
            if isinstance(exc, openai.BadRequestError):
                return LLMService._compose_exception_detail(exc, "LLM bad request")
            if isinstance(exc, openai.NotFoundError):
                return LLMService._compose_exception_detail(exc, "LLM resource not found")
            if isinstance(exc, openai.ConflictError):
                return LLMService._compose_exception_detail(exc, "LLM request conflict")
            if isinstance(exc, openai.UnprocessableEntityError):
                return LLMService._compose_exception_detail(exc, "LLM unprocessable entity")
            if isinstance(exc, openai.InternalServerError):
                return LLMService._compose_exception_detail(exc, "LLM upstream internal server error")
            if isinstance(exc, openai.APIStatusError):
                return LLMService._compose_exception_detail(exc, "LLM API status error")

        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            return f"{exc!r}; cause={cause!r}"
        return repr(exc)

    @staticmethod
    def _compose_exception_detail(exc: Exception, label: str) -> str:
        parts = [label]

        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            parts.append(f"status_code={status_code}")

        request_id = getattr(exc, "request_id", None)
        if request_id:
            parts.append(f"request_id={request_id}")

        body = None
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                body = response.text
            except Exception:
                body = None
        if body:
            body_preview = " ".join(str(body).split())[:240]
            parts.append(f"body={body_preview}")

        message = str(exc).strip()
        if message:
            parts.append(f"message={message}")

        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            parts.append(f"cause={cause!r}")

        return " | ".join(parts)


llm_service = LLMService()
