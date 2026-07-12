from fastapi import APIRouter

from app.api.routers import attachments, auth, chat, eval, health, projects, search


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(attachments.router, prefix="/attachments", tags=["attachments"])
api_router.include_router(eval.router, prefix="/eval", tags=["eval"])
