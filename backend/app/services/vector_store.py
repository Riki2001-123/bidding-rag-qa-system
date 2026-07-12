from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from app.core.config import settings
from app.services.domain_config import VALID_DOMAINS

try:
    from pymilvus import DataType, MilvusClient
except Exception as exc:  # pragma: no cover
    DataType = None
    MilvusClient = None
    _MILVUS_IMPORT_ERROR = exc
else:  # pragma: no cover
    _MILVUS_IMPORT_ERROR = None


VARCHAR_MAX_LENGTH = 512
VECTOR_FIELD_NAME = "vector"
PRIMARY_FIELD_NAME = "chunk_id"
DEFAULT_INDEX_NAME = "vector_index"
DEFAULT_INDEX_TYPE = "IVF_FLAT"
DEFAULT_INDEX_PARAMS = {"nlist": 128}
DEFAULT_SEARCH_PARAMS = {"metric_type": "IP", "params": {"nprobe": 10}}


class VectorStore:
    """Milvus-backed vector store."""

    def __init__(
        self,
        *,
        client: Optional[Any] = None,
        client_cls: Optional[Any] = None,
        datatype_cls: Optional[Any] = None,
    ) -> None:
        self._client = client
        self._client_cls = client_cls if client_cls is not None else MilvusClient
        self._datatype_cls = datatype_cls if datatype_cls is not None else DataType
        self._collection_prefix = settings.milvus_collection_prefix.strip() or "rag"
        self._dimension = settings.embedding_dimension
        self._lock = threading.Lock()
        self._initialized_domains: set[str] = set()

    @property
    def uri(self) -> str:
        return f"http://{settings.milvus_host}:{settings.milvus_port}"

    def _token(self) -> Optional[str]:
        if not settings.milvus_user and not settings.milvus_password:
            return None
        return f"{settings.milvus_user}:{settings.milvus_password}"

    def _get_client(self):
        if self._client is not None:
            return self._client

        if self._client_cls is None:
            raise RuntimeError(f"pymilvus is not installed: {_MILVUS_IMPORT_ERROR!r}")

        client_kwargs: Dict[str, Any] = {"uri": self.uri}
        token = self._token()
        if token:
            client_kwargs["token"] = token
        if settings.milvus_db_name:
            client_kwargs["db_name"] = settings.milvus_db_name

        try:
            self._client = self._client_cls(**client_kwargs)
        except Exception as exc:
            raise RuntimeError(f"failed to connect to Milvus at {self.uri}: {exc}") from exc
        return self._client

    def _collection_name(self, domain: str) -> str:
        self._validate_domain(domain)
        return f"{self._collection_prefix}_{domain}"

    @staticmethod
    def _validate_domain(domain: str) -> None:
        if domain not in VALID_DOMAINS:
            raise ValueError(f"unsupported domain: {domain}")

    @staticmethod
    def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
        if vectors.size == 0:
            return vectors.astype("float32")
        normalized = vectors.astype("float32", copy=True)
        norms = np.linalg.norm(normalized, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normalized /= norms
        return normalized

    def _normalize_vector(self, vector: Any) -> np.ndarray:
        values = np.asarray(vector, dtype="float32")
        if values.ndim == 0:
            values = values.reshape(1)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        normalized = self._normalize_rows(values)
        if normalized.shape[1] != self._dimension:
            raise RuntimeError(
                f"vector dimension mismatch: expected {self._dimension}, got {normalized.shape[1]}"
            )
        return normalized

    def _create_schema(self):
        if self._datatype_cls is None or self._client_cls is None:
            raise RuntimeError(f"pymilvus is not installed: {_MILVUS_IMPORT_ERROR!r}")

        schema = self._client_cls.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=self._datatype_cls.INT64, is_primary=True)
        schema.add_field(field_name="record_id", datatype=self._datatype_cls.INT64)
        schema.add_field(field_name="source_field", datatype=self._datatype_cls.VARCHAR, max_length=VARCHAR_MAX_LENGTH)
        schema.add_field(field_name="chunk_key", datatype=self._datatype_cls.VARCHAR, max_length=VARCHAR_MAX_LENGTH)
        schema.add_field(field_name=VECTOR_FIELD_NAME, datatype=self._datatype_cls.FLOAT_VECTOR, dim=self._dimension)
        return schema

    def _create_index_params(self):
        if self._client_cls is None:
            raise RuntimeError(f"pymilvus is not installed: {_MILVUS_IMPORT_ERROR!r}")

        index_params = self._client_cls.prepare_index_params()
        index_params.add_index(
            field_name=VECTOR_FIELD_NAME,
            index_name=DEFAULT_INDEX_NAME,
            index_type=DEFAULT_INDEX_TYPE,
            metric_type="IP",
            params=DEFAULT_INDEX_PARAMS,
        )
        return index_params

    def _verify_existing_schema(self, domain: str) -> None:
        client = self._get_client()
        description = client.describe_collection(collection_name=self._collection_name(domain))
        fields = {field.get("name"): field for field in description.get("fields", [])}
        required_fields = {"chunk_id", "record_id", "source_field", "chunk_key", VECTOR_FIELD_NAME}
        missing = required_fields - set(fields)
        if missing:
            raise RuntimeError(
                f"Milvus collection {self._collection_name(domain)} is missing fields: {', '.join(sorted(missing))}"
            )

        vector_field = fields[VECTOR_FIELD_NAME]
        raw_dim = vector_field.get("params", {}).get("dim")
        try:
            actual_dim = int(raw_dim)
        except (TypeError, ValueError):
            raise RuntimeError(
                f"Milvus collection {self._collection_name(domain)} has invalid vector dim: {raw_dim!r}"
            ) from None
        if actual_dim != self._dimension:
            raise RuntimeError(
                f"Milvus collection {self._collection_name(domain)} dim mismatch: "
                f"expected {self._dimension}, got {actual_dim}"
            )

    def _load_collection(self, domain: str) -> None:
        client = self._get_client()
        client.load_collection(collection_name=self._collection_name(domain))

    def ensure_collections(self, domains: Optional[Sequence[str]] = None, *, force: bool = False) -> None:
        target_domains = list(domains or VALID_DOMAINS)
        with self._lock:
            for domain in target_domains:
                self._ensure_collection(domain, force=force)

    def _ensure_collection(self, domain: str, *, force: bool = False) -> None:
        self._validate_domain(domain)
        if not force and domain in self._initialized_domains:
            return

        client = self._get_client()
        collection_name = self._collection_name(domain)
        if client.has_collection(collection_name=collection_name):
            self._verify_existing_schema(domain)
        else:
            client.create_collection(
                collection_name=collection_name,
                schema=self._create_schema(),
                index_params=self._create_index_params(),
            )
        self._load_collection(domain)
        self._initialized_domains.add(domain)

    def check_connection(self, *, ensure_collections: bool = True) -> None:
        if ensure_collections:
            self.ensure_collections()
            return
        self._get_client()

    def upsert(self, domain: str, embeddings: List[dict]) -> None:
        if not embeddings:
            return

        self.ensure_collections([domain])
        client = self._get_client()
        rows = []
        for item in embeddings:
            vector = self._normalize_vector(item["vector"])[0].tolist()
            rows.append(
                {
                    "chunk_id": int(item["chunk_id"]),
                    "record_id": int(item["record_id"]),
                    "source_field": str(item["source_field"]),
                    "chunk_key": str(item.get("metadata", {}).get("chunk_key", "")),
                    VECTOR_FIELD_NAME: vector,
                }
            )
        client.upsert(collection_name=self._collection_name(domain), data=rows)
        self._load_collection(domain)

    def search(self, domain: str, query_vector: np.ndarray, top_k: int = 5) -> List[dict]:
        self.ensure_collections([domain])
        client = self._get_client()
        query = self._normalize_vector(query_vector)[0].tolist()
        raw_results = client.search(
            collection_name=self._collection_name(domain),
            data=[query],
            anns_field=VECTOR_FIELD_NAME,
            limit=max(int(top_k), 1),
            output_fields=["chunk_id", "record_id", "source_field", "chunk_key"],
            search_params=DEFAULT_SEARCH_PARAMS,
        )

        hits = raw_results[0] if raw_results and isinstance(raw_results[0], list) else raw_results
        normalized_hits = []
        for hit in hits or []:
            entity = hit.get("entity") or {}
            chunk_id = entity.get("chunk_id", hit.get("id"))
            if chunk_id is None:
                continue
            normalized_hits.append(
                {
                    "chunk_id": int(chunk_id),
                    "record_id": int(entity.get("record_id", 0) or 0),
                    "source_field": entity.get("source_field", "") or "",
                    "score": float(hit.get("distance", hit.get("score", 0.0))),
                    "metadata": {"chunk_key": entity.get("chunk_key", "") or ""},
                }
            )
        return normalized_hits

    def delete(self, domain: str, chunk_ids: List[int]) -> None:
        if not chunk_ids:
            return
        self.ensure_collections([domain])
        self._get_client().delete(collection_name=self._collection_name(domain), ids=[int(item) for item in chunk_ids])

    def rebuild(self, domain: str, embeddings: List[dict]) -> None:
        client = self._get_client()
        collection_name = self._collection_name(domain)
        if client.has_collection(collection_name=collection_name):
            client.drop_collection(collection_name=collection_name)
        self._initialized_domains.discard(domain)
        self.ensure_collections([domain], force=True)
        if embeddings:
            self.upsert(domain, embeddings)


vector_store = VectorStore()
