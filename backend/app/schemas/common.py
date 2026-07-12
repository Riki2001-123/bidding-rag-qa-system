from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class APIMessage(BaseModel):
    message: str


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    display_name: str


class ProjectOut(BaseModel):
    id: int
    name: str
    code: str
    description: str


class AttachmentOut(BaseModel):
    id: int
    domain: str
    record_id: int
    original_name: str
    project_id: Optional[int] = None
    access_level: str
    download_url: str


class CitationOut(BaseModel):
    domain: str
    record_id: int
    title: str
    score: float
    source_fields: List[str] = Field(default_factory=list)
    key_fields: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[AttachmentOut] = Field(default_factory=list)


class SearchResultOut(BaseModel):
    domain: str
    record_id: int
    score: float
    title: str
    summary: str
    publish_date: Optional[date] = None
    key_fields: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[AttachmentOut] = Field(default_factory=list)


class ImportJobOut(BaseModel):
    id: int
    domain: str
    file_name: str
    status: str
    error_report: str
    total_rows: int
    success_rows: int
    failed_rows: int
    created_at: datetime
    updated_at: datetime
