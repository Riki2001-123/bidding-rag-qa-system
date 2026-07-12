from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import Attachment, User
from app.schemas.common import AttachmentOut
from app.services.retrieval import apply_permission_filters


router = APIRouter()

# ── 上传安全配置 ──────────────────────────────────────────────────

ALLOWED_DOMAINS = {"tender", "enterprise", "policy"}

ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".zip", ".rar", ".7z", ".csv", ".txt", ".json", ".xml",
}

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/upload", response_model=AttachmentOut)
async def upload_attachment(
    domain: str = Form(...),
    record_id: int = Form(...),
    project_id: Optional[int] = Form(default=None),
    access_level: str = Form(default="public"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 校验 domain 枚举
    if domain not in ALLOWED_DOMAINS:
        raise HTTPException(status_code=400, detail=f"不支持的域: {domain}，允许值: {', '.join(sorted(ALLOWED_DOMAINS))}")

    # 校验文件名非空
    if not file.filename or not file.filename.strip():
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 校验文件扩展名
    suffix = Path(file.filename).suffix.lower()
    if not suffix or suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix or '(无扩展名)'}")

    # 流式读取并校验文件大小（避免大文件 OOM）
    content = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB 分块
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail=f"文件大小超过 {MAX_UPLOAD_SIZE // (1024 * 1024)} MB 限制")

    stored_name = f"{uuid4().hex}{suffix}"
    target_path = settings.attachment_dir / stored_name
    target_path.write_bytes(bytes(content))

    attachment = Attachment(
        domain=domain,
        record_id=record_id,
        filename=stored_name,
        storage_path=str(target_path),
        original_name=file.filename,
        uploaded_by=current_user.id,
        project_id=project_id,
        access_level=access_level,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return AttachmentOut(
        id=attachment.id,
        domain=attachment.domain,
        record_id=attachment.record_id,
        original_name=attachment.original_name,
        project_id=attachment.project_id,
        access_level=attachment.access_level,
        download_url=f"/api/attachments/{attachment.id}/download",
    )


@router.get("/{attachment_id}/download")
def download_attachment(attachment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    stmt = select(Attachment).where(Attachment.id == attachment_id)
    stmt = apply_permission_filters(stmt, Attachment, db, current_user)
    attachment = db.scalar(stmt)
    if not attachment:
        raise HTTPException(status_code=404, detail="附件不存在或无权限访问")
    return FileResponse(path=attachment.storage_path, filename=attachment.original_name)
