"""
数据迁移脚本：将旧的 json+npy 向量存储迁移到 ChromaDB。

用法:
    cd D:\python\PythonProject\RAG+LLMProject\backend
    python -m scripts.migrate_to_chroma

脚本会:
    1. 扫描 storage/vector_store/ 下的 {domain}.json 和 {domain}.npy 文件
    2. 读取旧的向量数据
    3. 通过 VectorStore.upsert() 写入 ChromaDB
    4. 备份旧文件到 storage/vector_store/_legacy/

注意: 运行前确保已安装 chromadb 和 langchain-community:
    pip install chromadb langchain-community
"""

import json
import shutil
from pathlib import Path

import numpy as np

# 确保项目根目录在 Python 路径中
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings


def find_legacy_data(base_dir: Path) -> list:
    """扫描旧的 json+npy 数据文件。"""
    domains = []
    for json_file in sorted(base_dir.glob("*.json")):
        npy_file = json_file.with_suffix(".npy")
        if npy_file.exists():
            domains.append({
                "domain": json_file.stem,
                "json_path": json_file,
                "npy_path": npy_file,
            })
    return domains


def load_legacy_state(json_path: Path, npy_path: Path) -> dict:
    """加载旧的向量状态。"""
    items = json.loads(json_path.read_text(encoding="utf-8"))
    vectors = np.load(npy_path, allow_pickle=False)
    return {
        "items": items,
        "vectors": [vectors[i] for i in range(vectors.shape[0])],
    }


def migrate():
    """执行迁移。"""
    vector_dir = settings.vector_store_dir

    if not vector_dir.exists():
        print(f"向量存储目录不存在: {vector_dir}")
        print("没有需要迁移的数据。")
        return

    # 查找旧数据
    legacy_domains = find_legacy_data(vector_dir)

    if not legacy_domains:
        print("未找到旧的 json+npy 数据文件，无需迁移。")
        return

    print(f"找到 {len(legacy_domains)} 个域的数据需要迁移:")
    for d in legacy_domains:
        print(f"  - {d['domain']}: {d['json_path'].name}")

    # 导入 VectorStore（这会加载新的 ChromaDB 版本）
    from app.services.vector_store import vector_store

    # 备份目录
    legacy_backup = vector_dir / "_legacy"
    legacy_backup.mkdir(parents=True, exist_ok=True)

    total_chunks = 0

    for domain_info in legacy_domains:
        domain = domain_info["domain"]
        print(f"\n正在迁移 [{domain}]...")

        try:
            state = load_legacy_state(domain_info["json_path"], domain_info["npy_path"])
            items = state["items"]
            vectors = state["vectors"]

            if not items:
                print(f"  [{domain}] 无数据，跳过。")
                continue

            # 构造 embeddings 列表
            embeddings = []
            for item, vector in zip(items, vectors):
                embeddings.append({
                    "chunk_id": item["chunk_id"],
                    "record_id": item["record_id"],
                    "source_field": item["source_field"],
                    "metadata": item.get("metadata", {}),
                    "vector": vector.tolist() if isinstance(vector, np.ndarray) else vector,
                })

            print(f"  [{domain}] 写入 {len(embeddings)} 条向量到 ChromaDB...")
            vector_store.upsert(domain, embeddings)
            total_chunks += len(embeddings)

            # 备份旧文件
            shutil.copy2(domain_info["json_path"], legacy_backup / domain_info["json_path"].name)
            shutil.copy2(domain_info["npy_path"], legacy_backup / domain_info["npy_path"].name)
            print(f"  [{domain}] 已备份旧文件到 {legacy_backup}")

        except Exception as e:
            print(f"  [{domain}] 迁移失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n迁移完成！共迁移 {total_chunks} 条向量。")
    print(f"旧数据备份位置: {legacy_backup}")
    print("\n如果确认迁移成功，可以手动删除 _legacy 目录。")


if __name__ == "__main__":
    migrate()
