import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(ENV_PATH)


@dataclass
class Settings:
    app_name: str
    app_env: str
    secret_key: str
    access_token_ttl_seconds: int
    password_hash_iterations: int
    database_url: str
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    openai_timeout_seconds: int
    openai_max_retries: int
    embedding_model: str
    embedding_dimension: int
    reranker_enabled: bool
    reranker_model: str
    milvus_host: str
    milvus_port: int
    milvus_user: str
    milvus_password: str
    milvus_db_name: str
    milvus_collection_prefix: str
    attachment_dir: Path
    cors_origins: List[str]
    repo_root: Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _to_path(raw: str, root: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_settings() -> Settings:
    root = _repo_root()
    origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    return Settings(
        app_name=os.getenv("APP_NAME", "招投标采购智能问答系统"),
        app_env=os.getenv("APP_ENV", "development"),
        secret_key=os.getenv("SECRET_KEY", "change-me"),
        access_token_ttl_seconds=int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "3600")),
        password_hash_iterations=int(os.getenv("PASSWORD_HASH_ITERATIONS", "390000")),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./storage/app.db"),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_timeout_seconds=int(os.getenv("OPENAI_TIMEOUT_SECONDS", "15")),
        openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
        embedding_model=os.getenv("EMBEDDING_MODEL", ""),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
        reranker_enabled=os.getenv("RERANKER_ENABLED", "false").lower() == "true",
        reranker_model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base-zh-v1.5"),
        milvus_host=os.getenv("MILVUS_HOST", "127.0.0.1"),
        milvus_port=int(os.getenv("MILVUS_PORT", "19530")),
        milvus_user=os.getenv("MILVUS_USER", ""),
        milvus_password=os.getenv("MILVUS_PASSWORD", ""),
        milvus_db_name=os.getenv("MILVUS_DB_NAME", "default"),
        milvus_collection_prefix=os.getenv("MILVUS_COLLECTION_PREFIX", "rag"),
        attachment_dir=_to_path(os.getenv("ATTACHMENT_DIR", "storage/attachments"), root),
        cors_origins=[item.strip() for item in origins.split(",") if item.strip()],
        repo_root=root,
    )


settings = load_settings()
