from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Project, User
from app.schemas.common import ProjectOut


router = APIRouter()


@router.get("", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _ = current_user
    items = db.scalars(select(Project).order_by(Project.id.asc())).all()
    return [ProjectOut(id=item.id, name=item.name, code=item.code, description=item.description) for item in items]
