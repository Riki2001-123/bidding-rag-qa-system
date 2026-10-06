import json
from typing import AsyncIterable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ChatMessage, ChatSession, User
from app.schemas.common import CitationOut
from app.services.chat_agents import ChatOrchestrator
from app.services.conversation_memory import conversation_memory


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
    completed = False
    try:
        session = _get_or_create_session(db, user, session, question)
        result = get_chat_orchestrator().orchestrate(
            db=db, user=user, question=question, preferred_domain=domain,
            top_k=top_k, history_messages=history_messages, session_id=session.id,
        )
        response = _persist_answer(
            db=db, user=user, session=session, question=question,
            resolved_domain=result.domain, answer=result.answer,
            citations=result.citations, conservative=result.conservative,
        )
        completed = True
        return response
    finally:
        if not completed:
            db.rollback()
            if session is not None:
                conversation_memory.clear_session(session.id)


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
    生成与持久化成功后，type=done 事件携带完整 answer 和 session_id。
    """
    session = _get_owned_session(db, user, session_id)
    history_messages = _load_history_messages(db, session)

    meta_info = None
    done_info = None
    completed = False
    try:
        session = _get_or_create_session(db, user, session, question)
        async for sse_chunk in get_chat_orchestrator().stream_orchestrate(
            db=db,
            user=user,
            question=question,
            preferred_domain=domain,
            top_k=top_k,
            history_messages=history_messages,
            session_id=session.id if session else None,
        ):
            payload = None
            if sse_chunk.startswith("data: "):
                payload = json.loads(sse_chunk[6:].strip())
            if payload and payload.get("type") == "meta":
                meta_info = payload
            if payload and payload.get("type") == "error":
                yield sse_chunk
                return
            if payload and payload.get("type") == "done":
                # Complete only after the source stream and persistence succeed.
                done_info = payload
            else:
                yield sse_chunk

        if not meta_info or not done_info or not done_info.get("answer"):
            raise RuntimeError("Incomplete answer stream")
        citations_out = [CitationOut(**citation) for citation in meta_info.get("citations", [])]
        result = _persist_answer(
            db=db,
            user=user,
            session=session,
            question=question,
            resolved_domain=meta_info.get("domain", ""),
            answer=done_info["answer"],
            citations=citations_out,
            conservative=meta_info.get("conservative", False),
        )
        done_info["session_id"] = result["session_id"]
        completed = True
        yield f"data: {json.dumps(done_info, ensure_ascii=False)}\n\n"
    except Exception:
        # Do not expose provider errors, credentials or a fabricated success.
        error = {"type": "error", "message": "回答生成或保存失败，请稍后重试。"}
        yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
    finally:
        if not completed:
            db.rollback()
            if session is not None:
                conversation_memory.clear_session(session.id)


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
