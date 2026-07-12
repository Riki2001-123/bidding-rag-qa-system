from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import ChatMessage, ChatSession, User
from app.schemas.chat import ChatMessageOut, ChatRequest, ChatResponse, ChatSessionOut
from app.services.chat import answer_question, stream_answer_question


router = APIRouter()


@router.post("/query", response_model=ChatResponse)
def query_chat(payload: ChatRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = answer_question(db, current_user, payload.question, payload.domain, payload.session_id, payload.top_k)
    return ChatResponse(**result)


@router.post("/query/stream")
async def query_chat_stream(payload: ChatRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """SSE 流式问答端点，逐 chunk 返回文本。"""
    return StreamingResponse(
        stream_answer_question(
            db=db,
            user=current_user,
            question=payload.question,
            domain=payload.domain,
            session_id=payload.session_id,
            top_k=payload.top_k,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionOut)
def get_session(session_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = db.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == current_user.id))
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id.asc())).all()
    return ChatSessionOut(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[
            ChatMessageOut(
                id=item.id,
                role=item.role,
                question_domain=item.question_domain,
                content=item.content,
                citations_json=item.citations_json,
                created_at=item.created_at,
            )
            for item in messages
        ],
    )

