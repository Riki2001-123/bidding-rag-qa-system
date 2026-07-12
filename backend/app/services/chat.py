import json
from typing import AsyncIterable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ChatMessage, ChatSession, User
from app.schemas.common import CitationOut
from app.services.chat_agents import ChatOrchestrator


_chat_orchestrator = ChatOrchestrator()


def get_chat_orchestrator() -> ChatOrchestrator:
    return _chat_orchestrator


def answer_question(
    db: Session,
    user: User,
    question: str,
    domain: Optional[str],
    session_id: Optional[int],
    top_k: int,
):
    session = _get_owned_session(db, user, session_id)
    history_messages = _load_history_messages(db, session)
    result = get_chat_orchestrator().orchestrate(
        db=db,
        user=user,
        question=question,
        preferred_domain=domain,
        top_k=top_k,
        history_messages=history_messages,
        session_id=session.id if session else None,
    )
    return _persist_answer(
        db=db,
        user=user,
        session=session,
        question=question,
        resolved_domain=result.domain,
        answer=result.answer,
        citations=result.citations,
        conservative=result.conservative,
    )


async def stream_answer_question(
    db: Session,
    user: User,
    question: str,
    domain: Optional[str],
    session_id: Optional[int],
    top_k: int,
) -> AsyncIterable[str]:
    """
    流式问答：先完成路由和检索，再逐 chunk 流式输出回答。
    每个 yield 是 SSE 格式字符串（"data: {...}\n\n"）。
    最后一个 type=done 事件携带完整 answer 用于持久化。
    """
    session = _get_owned_session(db, user, session_id)
    history_messages = _load_history_messages(db, session)

    full_answer = ""
    meta_info = None
    citations_raw = []

    async for sse_chunk in get_chat_orchestrator().stream_orchestrate(
        db=db,
        user=user,
        question=question,
        preferred_domain=domain,
        top_k=top_k,
        history_messages=history_messages,
        session_id=session.id if session else None,
    ):
        yield sse_chunk
        # 解析 SSE data 提取 meta 和 done
        if sse_chunk.startswith("data: "):
            try:
                payload = json.loads(sse_chunk[6:].strip())
            except (json.JSONDecodeError, IndexError):
                continue
            if payload.get("type") == "meta":
                meta_info = payload
                citations_raw = payload.get("citations", [])
            elif payload.get("type") == "done":
                full_answer = payload.get("answer", "")

    # 持久化到数据库
    if meta_info and full_answer:
        from app.schemas.common import CitationOut as _CitationOut
        resolved_domain = meta_info.get("domain", "")
        conservative = meta_info.get("conservative", False)
        citations_out = [
            _CitationOut(
                domain=c.get("domain", ""),
                record_id=c.get("record_id", 0),
                title=c.get("title", ""),
                score=c.get("score", 0.0),
                source_fields=c.get("source_fields", []),
                key_fields=c.get("key_fields", {}),
            )
            for c in citations_raw
        ]
        _persist_answer(
            db=db,
            user=user,
            session=session,
            question=question,
            resolved_domain=resolved_domain,
            answer=full_answer,
            citations=citations_out,
            conservative=conservative,
        )


def _get_owned_session(db: Session, user: User, session_id: Optional[int]) -> Optional[ChatSession]:
    if not session_id:
        return None
    return db.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id))


def _load_history_messages(db: Session, session: Optional[ChatSession]) -> List[Dict[str, str]]:
    if not session:
        return []

    history_msgs = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.id.asc())
    ).all()
    return [{"role": msg.role, "content": msg.content} for msg in history_msgs]


def _persist_answer(
    db: Session,
    user: User,
    session: Optional[ChatSession],
    question: str,
    resolved_domain: str,
    answer: str,
    citations: List[CitationOut],
    conservative: bool,
):
    session = _get_or_create_session(db, user, session, question)
    db.add(ChatMessage(session_id=session.id, role="user", question_domain=resolved_domain, content=question))
    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            question_domain=resolved_domain,
            content=answer,
            citations_json=json.dumps([citation.model_dump() for citation in citations], ensure_ascii=False),
        )
    )
    db.commit()
    return {
        "session_id": session.id,
        "domain": resolved_domain,
        "answer": answer,
        "citations": citations,
        "conservative": conservative,
    }


def _get_or_create_session(db: Session, user: User, session: Optional[ChatSession], question: str) -> ChatSession:
    if session:
        return session

    session = ChatSession(user_id=user.id, title=question[:30] or "新会话")
    db.add(session)
    db.flush()
    return session
