from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.common import CitationOut


class ChatRequest(BaseModel):
    question: str
    domain: Optional[str] = None
    session_id: Optional[int] = None
    top_k: int = Field(default=10, ge=1, le=50)


class ChatResponse(BaseModel):
    session_id: int
    domain: str
    answer: str
    citations: List[CitationOut] = Field(default_factory=list)
    conservative: bool = False


class ChatMessageOut(BaseModel):
    id: int
    role: str
    question_domain: str
    content: str
    citations_json: str
    created_at: datetime


class ChatSessionOut(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    messages: List[ChatMessageOut] = Field(default_factory=list)
