"""Reranker 服务 - 优先从本地加载，使用 FlagEmbedding"""
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.core.config import settings

# 不设置镜像，使用官方源

class RerankerService:
    """基于 FlagEmbedding 的精排服务"""
    
    _instance = None
    _model = None
    _load_attempted = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @property
    def enabled(self) -> bool:
        return settings.reranker_enabled
    
    def _get_model_path(self) -> Optional[str]:
        """优先使用显式本地路径，其次使用 backend/models 下的模型。"""
        configured_path = settings.reranker_model
        if configured_path:
            configured_local_path = Path(configured_path).expanduser()
            if configured_local_path.is_dir() and (configured_local_path / "config.json").exists():
                return str(configured_local_path.resolve())

            configured_repo_path = Path(__file__).resolve().parents[2] / configured_path
            if configured_repo_path.is_dir() and (configured_repo_path / "config.json").exists():
                return str(configured_repo_path)

            configured_backend_path = Path(__file__).resolve().parents[2] / "models" / configured_path.split("/")[-1]
            if configured_backend_path.is_dir() and (configured_backend_path / "config.json").exists():
                return str(configured_backend_path)

        backend_dir = Path(__file__).resolve().parents[2]
        local_path = backend_dir / "models" / "bge-reranker-base-zh-v1.5"
        if local_path.is_dir() and (local_path / "config.json").exists():
            return str(local_path)

        if configured_path:
            return configured_path
        return None
    
    def _load_model(self):
        if self._model is None and self.enabled:
            if self._load_attempted:
                return None
            self._load_attempted = True
            try:
                from FlagEmbedding import FlagReranker
                model_path = self._get_model_path()
                if not model_path:
                    print("[Reranker] 未找到本地模型，已跳过精排加载")
                    return None
                print(f"[Reranker] 正在加载模型: {model_path}")
                self._model = FlagReranker(model_path, use_fp16=False)
                print(f"[Reranker] 模型加载完成")
            except Exception as e:
                print(f"[Reranker] 模型加载失败: {e}")
                self._model = None
        return self._model
    
    def rerank(self, query: str, passages: List[str], top_k: int = 5) -> List[Dict[str, Any]]:
        model = self._load_model()
        if model is None or not passages:
            return [{"index": i, "score": 0.5} for i in range(min(top_k, len(passages)))]
        
        pairs = [[query, p] for p in passages]
        scores = model.compute_score(pairs, normalize=True)
        
        if not isinstance(scores, list):
            scores = scores.tolist() if hasattr(scores, 'tolist') else [scores] * len(passages)
        
        indexed_scores = [{"index": i, "score": float(s)} for i, s in enumerate(scores)]
        indexed_scores.sort(key=lambda x: x["score"], reverse=True)
        
        return indexed_scores[:top_k]

reranker_service = RerankerService()
