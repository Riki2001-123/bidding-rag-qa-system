from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import User
from app.schemas.auth import LoginRequest, LoginResponse
from app.schemas.common import UserInfo
from app.services.security import create_token, hash_password, needs_password_rehash, verify_password


router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if needs_password_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()
    return LoginResponse(
        access_token=create_token(user.id, user.username, user.role),
        user=UserInfo(id=user.id, username=user.username, role=user.role, display_name=user.display_name),
    )


@router.get("/me", response_model=UserInfo)
def me(current_user: User = Depends(get_current_user)):
    return UserInfo(id=current_user.id, username=current_user.username, role=current_user.role, display_name=current_user.display_name)
