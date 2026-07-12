# 招投标采购智能问答系统 V1

基于 `RAG + LLM + MySQL + Milvus` 的招投标采购问答系统骨架，业务主数据保存在 MySQL，文本切块元数据保存在 `text_chunks`，向量数据存放在 Milvus。

## 当前实现

- FastAPI 后端
- React + Vite 前端
- MySQL 业务表结构化检索
- `MySQL + BM25 + Milvus` 混合召回
- 可选 reranker 精排
- 简单登录、权限过滤、会话问答、附件关联

## 启动 Milvus

先确保本机已安装 Docker Desktop，并能执行 `docker compose`.

```bash
docker compose -f docker-compose.milvus.yml up -d
```

启动后默认开放：

- gRPC / SDK: `127.0.0.1:19530`
- Health / metrics: `127.0.0.1:9091`

## 后端启动

```bash
pip install -r backend/requirements.txt
copy backend\\.env.example backend\\.env
uvicorn app.main:app --reload --app-dir backend
```

`backend/.env` 至少需要配置：

```env
DATABASE_URL=mysql+pymysql://user:password@127.0.0.1:3306/xunfei07_rag_db?charset=utf8mb4
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_DB_NAME=default
MILVUS_COLLECTION_PREFIX=rag
```

如果 Milvus 开启了认证，再补：

```env
MILVUS_USER=your-user
MILVUS_PASSWORD=your-password
```

## 索引同步

从 `policy_records`、`tender_records`、`enterprise_records` 全量重建文本块并写入 Milvus：

```bash
python -m backend.scripts.sync_mysql_index
```

只同步单个领域：

```bash
python -m backend.scripts.sync_mysql_index --domain tender
```

从现有 `text_chunks` 全量重建 Milvus collection：

```bash
python -m backend.scripts.rebuild_milvus_index
```

## 默认账号

- `admin / admin123`
- `internal / internal123`
- `supplier / supplier123`
