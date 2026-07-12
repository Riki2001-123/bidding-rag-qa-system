from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.init_db import initialize_database
from app.services.vector_store import vector_store


app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    initialize_database()
    try:
        vector_store.check_connection(ensure_collections=True)
        print("[Milvus] connection ready")
    except Exception as exc:
        print(f"[Milvus] startup check failed: {exc}")


app.include_router(api_router, prefix="/api")
