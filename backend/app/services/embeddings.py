import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

import numpy as np
import requests
from tqdm import tqdm

from app.core.config import settings

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

try:
    from huggingface_hub import try_to_load_from_cache
except Exception:  # pragma: no cover
    try_to_load_from_cache = None

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except Exception:  # pragma: no cover
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings
    except Exception:
        HuggingFaceEmbeddings = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover
    SentenceTransformer = None

try:
    import torch
except Exception:  # pragma: no cover
    torch = None


class EmbeddingService:
    """Embedding service with DashScope API and local model support."""

    DASHSCOPE_PREFIX = "dashscope:"
    DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    DASHSCOPE_BATCH_LIMIT = 10  # DashScope embedding API 每次最多 10 条

    def __init__(self) -> None:
        self.dimension = settings.embedding_dimension
        self.model = None
        self._st_model = None
        self._initialized = False
        self._dashscope_model = None  # DashScope 模型名，如 text-embedding-v4
        self._use_dashscope = False

    def _running_in_test(self) -> bool:
        return "PYTEST_CURRENT_TEST" in os.environ or "UNITTEST_RUNNING" in os.environ or "unittest" in sys.modules

    def _resolve_model_name(self) -> Optional[str]:
        model_name = settings.embedding_model
        if not model_name:
            return None

        # DashScope API 模式：dashscope:text-embedding-v4
        if model_name.startswith(self.DASHSCOPE_PREFIX):
            return model_name

        model_path = Path(model_name).expanduser()
        if model_path.exists():
            return str(model_path.resolve())

        repo_model_path = settings.repo_root / "backend" / "models" / model_name.split("/")[-1]
        if repo_model_path.exists():
            return str(repo_model_path)

        if try_to_load_from_cache is not None:
            marker_files = ("config.json", "modules.json", "sentence_bert_config.json")
            for marker in marker_files:
                try:
                    cached_marker = try_to_load_from_cache(model_name, marker)
                    if cached_marker:
                        return str(Path(cached_marker).resolve().parent)
                except Exception:
                    continue

        return model_name

    def _ensure_model(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        model_name = self._resolve_model_name()
        if not model_name:
            print("[Embedding] 未配置 EMBEDDING_MODEL，将使用 hash fallback")
            return

        # DashScope API 模式
        if model_name.startswith(self.DASHSCOPE_PREFIX):
            self._dashscope_model = model_name[len(self.DASHSCOPE_PREFIX):]
            api_key = settings.openai_api_key
            if not api_key:
                print("[Embedding] DashScope API 模式但未配置 OPENAI_API_KEY，将使用 hash fallback")
                return
            self._use_dashscope = True
            print(f"[Embedding] 使用 DashScope API 模式，模型: {self._dashscope_model}，维度: {self.dimension}")
            return

        # 本地模型模式
        print(f"[Embedding] 开始加载模型: {model_name}")
        t0 = time.time()

        if self._running_in_test():
            model_path = Path(model_name)
            if not model_path.exists() and not (settings.repo_root / "backend" / "models" / model_name.split("/")[-1]).exists():
                print("[Embedding] 测试环境未发现本地模型，将使用 hash fallback")
                return

        if HuggingFaceEmbeddings is not None:
            try:
                print("[Embedding] 尝试 HuggingFaceEmbeddings...")
                self.model = HuggingFaceEmbeddings(
                    model_name=model_name,
                    encode_kwargs={"normalize_embeddings": True},
                )
                print(f"[Embedding] HuggingFaceEmbeddings 加载完成，耗时 {time.time()-t0:.1f}s")
                return
            except Exception as e:
                print(f"[Embedding] HuggingFaceEmbeddings 失败: {e}")
                self.model = None

        if SentenceTransformer is not None:
            try:
                print("[Embedding] 尝试 SentenceTransformer...")
                self._st_model = SentenceTransformer(model_name)
                print(f"[Embedding] SentenceTransformer 加载完成，耗时 {time.time()-t0:.1f}s")
            except Exception as e:
                print(f"[Embedding] SentenceTransformer 失败: {e}")
                self._st_model = None

        if self.model is None and self._st_model is None:
            print("[Embedding] 未能加载真实 embedding 模型，将使用 hash fallback")

    def _sentence_transformer_client(self) -> Any:
        if self.model is not None and hasattr(self.model, "client"):
            return self.model.client
        if self._st_model is not None:
            return self._st_model
        return None

    @staticmethod
    def _to_numpy_vectors(vectors: Any) -> List[np.ndarray]:
        if isinstance(vectors, np.ndarray):
            if vectors.ndim == 1:
                return [np.asarray(vectors, dtype="float32")]
            return [np.asarray(vector, dtype="float32") for vector in vectors]
        return [np.asarray(vector, dtype="float32") for vector in vectors]

    def _target_devices(self, num_workers: int) -> Optional[List[str]]:
        if num_workers <= 1:
            return None

        if torch is not None and torch.cuda.is_available():
            device_count = torch.cuda.device_count()
            if device_count > 1:
                return [f"cuda:{index % device_count}" for index in range(num_workers)]
            return None

        return ["cpu"] * num_workers

    def _dashscope_embed(self, texts: List[str], text_type: str = "document",
                         *, show_progress: bool = True, desc: str = "Embedding") -> List[np.ndarray]:
        """调用 DashScope Embedding API 生成向量"""
        api_key = settings.openai_api_key
        if not api_key:
            raise RuntimeError("DashScope embedding 需要 OPENAI_API_KEY")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        all_vectors = [None] * len(texts)
        max_retries = 3
        total_batches = math.ceil(len(texts) / self.DASHSCOPE_BATCH_LIMIT)

        pbar = tqdm(
            total=len(texts),
            desc=desc,
            unit="条",
            disable=not show_progress,
            ncols=100,
        )

        for i in range(0, len(texts), self.DASHSCOPE_BATCH_LIMIT):
            batch = texts[i : i + self.DASHSCOPE_BATCH_LIMIT]
            batch_idx = list(range(i, i + len(batch)))

            for attempt in range(max_retries):
                try:
                    payload = {
                        "model": self._dashscope_model,
                        "input": batch,
                        "dimensions": self.dimension,
                        "encoding_format": "float",
                    }

                    resp = requests.post(
                        self.DASHSCOPE_API_URL,
                        headers=headers,
                        json=payload,
                        timeout=30,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    for item in data.get("data", []):
                        idx = item.get("index", 0)
                        real_idx = batch_idx[idx]
                        all_vectors[real_idx] = np.array(
                            item["embedding"], dtype="float32"
                        )
                    pbar.update(len(batch))
                    break  # 成功则跳出重试

                except Exception as e:
                    if attempt < max_retries - 1:
                        wait = 2 ** (attempt + 1)
                        pbar.write(f"[Embedding] API 请求失败: {e}，{wait}s 后重试...")
                        time.sleep(wait)
                    else:
                        pbar.close()
                        raise RuntimeError(f"DashScope embedding API 失败（已重试 {max_retries} 次）: {e}")

            # API 限速控制
            if i + self.DASHSCOPE_BATCH_LIMIT < len(texts):
                time.sleep(0.15)

        pbar.close()

        # 检查是否有 None（漏掉的）
        missing = [i for i, v in enumerate(all_vectors) if v is None]
        if missing:
            raise RuntimeError(f"DashScope 返回缺少 {len(missing)} 条 embedding，索引: {missing[:10]}")

        return all_vectors

    def embed_texts(self, texts: Iterable[str]) -> List[np.ndarray]:
        self._ensure_model()
        values = [text or "" for text in texts]

        if self._use_dashscope:
            return self._dashscope_embed(values, text_type="document")

        if self.model is not None:
            vectors = self.model.embed_documents(values)
            return [np.asarray(vec, dtype="float32") for vec in vectors]

        if self._st_model is not None:
            vectors = self._st_model.encode(values, normalize_embeddings=True)
            return [np.asarray(vec, dtype="float32") for vec in vectors]

        return [self._hash_embed(text) for text in values]

    def embed_batch(
        self,
        texts: Iterable[str],
        batch_size: int = 64,
        num_workers: int = 1,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[np.ndarray]:
        self._ensure_model()
        values = [text or "" for text in texts]
        if not values:
            return []

        # DashScope API 模式：并发多线程加速
        if self._use_dashscope:
            workers = min(num_workers, max(num_workers, 4))  # 至少 4 线程
            batch_chunks = [
                values[i : i + batch_size]
                for i in range(0, len(values), batch_size)
            ]

            if workers <= 1:
                vectors = []
                total = len(values)
                for start in range(0, total, batch_size):
                    batch_values = values[start : start + batch_size]
                    vectors.extend(self._dashscope_embed(batch_values, text_type="document"))
                    if progress_callback is not None:
                        progress_callback(min(start + len(batch_values), total), total)
                return vectors

            # 多线程并发
            results = [None] * len(batch_chunks)
            total = len(values)

            def _worker(chunk_idx, chunk_texts):
                results[chunk_idx] = self._dashscope_embed(chunk_texts, text_type="document")
                processed = min((chunk_idx + 1) * batch_size, total)
                if progress_callback is not None:
                    progress_callback(processed, total)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(_worker, idx, chunk)
                    for idx, chunk in enumerate(batch_chunks)
                ]
                for f in futures:
                    f.result()  # 等待所有完成，抛出异常

            return [vec for batch_vectors in results for vec in batch_vectors]

        # 本地模型模式（原有逻辑）
        client = self._sentence_transformer_client()
        if client is not None:
            target_devices = self._target_devices(num_workers)
            if (
                target_devices
                and target_devices[0].startswith("cuda")
                and hasattr(client, "start_multi_process_pool")
                and hasattr(client, "encode_multi_process")
            ):
                pool = client.start_multi_process_pool(target_devices=target_devices)
                try:
                    vectors = client.encode_multi_process(
                        values,
                        pool,
                        batch_size=batch_size,
                        normalize_embeddings=True,
                    )
                    return self._to_numpy_vectors(vectors)
                finally:
                    if hasattr(client, "stop_multi_process_pool"):
                        client.stop_multi_process_pool(pool)

            if num_workers > 1:
                batches = [values[index : index + batch_size] for index in range(0, len(values), batch_size)]

                def _encode(batch_values: List[str]) -> List[np.ndarray]:
                    vectors = client.encode(
                        batch_values,
                        batch_size=batch_size,
                        convert_to_numpy=True,
                        normalize_embeddings=True,
                    )
                    return self._to_numpy_vectors(vectors)

                with ThreadPoolExecutor(max_workers=num_workers) as executor:
                    results = list(executor.map(_encode, batches))
                return [vector for batch_vectors in results for vector in batch_vectors]

            vectors: List[np.ndarray] = []
            total = len(values)
            for start in range(0, total, batch_size):
                batch_values = values[start : start + batch_size]
                batch_vectors = client.encode(
                    batch_values,
                    batch_size=batch_size,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
                vectors.extend(self._to_numpy_vectors(batch_vectors))
                if progress_callback is not None:
                    progress_callback(min(start + len(batch_values), total), total)
            return vectors

        if self.model is not None:
            vectors: List[np.ndarray] = []
            total = len(values)
            for start in range(0, total, batch_size):
                batch_values = values[start : start + batch_size]
                batch_vectors = self.model.embed_documents(batch_values)
                vectors.extend(np.asarray(vec, dtype="float32") for vec in batch_vectors)
                if progress_callback is not None:
                    progress_callback(min(start + len(batch_values), total), total)
            return vectors

        return [self._hash_embed(text) for text in values]

    def embed_query(self, text: str) -> np.ndarray:
        self._ensure_model()
        if self._use_dashscope:
            return self._dashscope_embed([text or ""], text_type="query")[0]
        if self.model is not None:
            vec = self.model.embed_query(text or "")
            return np.asarray(vec, dtype="float32")
        return self.embed_texts([text])[0]

    def _hash_embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dimension, dtype="float32")
        tokens = self._tokenize(text)
        if not tokens:
            return vec

        for token in tokens:
            index = abs(hash(token)) % self.dimension
            sign = 1.0 if abs(hash(token + "_sign")) % 2 == 0 else -1.0
            vec[index] += sign

        norm = math.sqrt(float(np.dot(vec, vec)))
        if norm > 0:
            vec /= norm
        return vec

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        text = (text or "").strip().lower()
        words = re.findall(r"[\w\u4e00-\u9fff]+", text)
        bigrams = [text[i : i + 2] for i in range(max(len(text) - 1, 0))]
        return words + bigrams


embedding_service = EmbeddingService()
