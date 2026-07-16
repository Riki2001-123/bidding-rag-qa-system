# Bidding & Procurement Q&A System (RAG + LLM + Multi-Agent)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-0.2-orange.svg)](https://langchain.com)
[![Milvus](https://img.shields.io/badge/Milvus-2.4+-brightgreen.svg)](https://milvus.io)

An enterprise-grade intelligent Q&A system for bidding and procurement, built with **RAG (Retrieval-Augmented Generation)**, **Multi-Agent orchestration**, and **hybrid search**. Covers three business domains — policies, tenders, and enterprises — with structured access control and streaming SSE output.

---

## Key Metrics

| Metric | Value |
|--------|-------|
| **Recall@10** | 93% (RRF fusion + Reranker) |
| **Hallucination Rate** | Reduced from 35% → 15% |
| **LLM-as-Judge Score** (100 samples) | Qwen-plus selected as optimal model |
| **Cross-domain retrieval latency** | Reduced from N×T to T (parallel execution) |
| **Supported question types** | 8 categories, 5 intents |
| **Codebase** | ~900 lines for core orchestration (refactored from 963) |

---

## Architecture

```
User Query
    │
    ▼
┌──────────────────────────────────────────────┐
│               JudgeAgent                      │
│       (Domain + Intent classification)        │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│            Query Rewriter                     │
│    (Coreference resolution + decomposition)   │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│        Adaptive Complexity Router             │
│                      │                        │
│     Simple Query      │    Complex Query       │
│          │            │         │              │
│          ▼            │         ▼              │
│   Standard RAG        │   ReAct Agent          │
│   (direct answer)     │   (multi-step loop)    │
└──────────┬────────────┴──────────┬────────────┘
           │                       │
           ▼                       ▼
┌──────────────────────────────────────────────┐
│             Hybrid Retrieval                  │
│  ┌──────────┐  ┌─────────┐  ┌─────────────┐ │
│  │ Milvus   │  │  BM25   │  │  Structured │ │
│  │ (Vector) │  │(Keyword)│  │   (SQL)     │ │
│  └────┬─────┘  └────┬────┘  └──────┬──────┘ │
│       │              │              │        │
│       └──────────────┼──────────────┘        │
│                      ▼                       │
│              RRF Fusion +                      │
│          BGE Reranker (Top-10)                │
│                      │                        │
│                      ▼                        │
│          Retrieval Validator                   │
│       (Low-confidence filtering)               │
└────────────────────────┬─────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────┐
│               LLM Answer Generation           │
│         (Qwen-plus with evidence chain)       │
└──────────────────────────────────────────────┘
```

### Retrieval Pipeline

1. **Vector Search** — BGE-large-zh-v1.5 (1024-dim) embeddings indexed in Milvus (IVF_FLAT)
2. **BM25** — jieba tokenizer + rank_bm25, weighted at 0.3 vs vector 0.7
3. **Structured Retrieval** — direct SQL queries on MySQL business tables
4. **RRF Fusion** — Reciprocal Rank Fusion (k=60) with source weights
5. **BGE Reranker** — Cross-encoder fine-rank to top-10
6. **Retrieval Validator** — rule-based quality gate (score < 0.3 → discard; >50% low-confidence → trigger supplemental retrieval)

### Agent Orchestration

- **JudgeAgent** identifies domain (policy/tender/enterprise) and intent (fact/filter/judgment/association/aggregate)
- **Query Rewriter** handles coreference resolution (e.g., "it", "the first one") and query decomposition
- **ReAct Agent** — custom implementation (no LangChain Agent dependency):
  - Thought-Action-Observation loop (max 3 steps)
  - Tool suite: SQL agent, semantic search, BM25 keyword search
  - Adaptive routing: simple queries → direct answer; complex queries → multi-step reasoning

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | FastAPI (async), Python 3.10+ |
| **LLM** | Qwen-turbo/plus/max via DashScope API |
| **Embedding** | BGE-large-zh-v1.5 (1024-dim) |
| **Vector DB** | Milvus 2.4 (IVF_FLAT index) |
| **Reranker** | BGE-reranker-base-zh-v1.5 |
| **BM25** | rank_bm25 + jieba |
| **Database** | MySQL (business data + text chunks) |
| **Frontend** | React 18 + Vite + Ant Design |
| **Evaluation** | LLM-as-Judge (qwen-max) + BERTScore + ROUGE-L |

---

## Project Structure

```
RAG+LLMProject/
├── backend/
│   ├── app/
│   │   ├── api/               # REST endpoints
│   │   ├── core/               # Config & settings
│   │   ├── db/                 # Database initialization
│   │   ├── models/             # SQLAlchemy ORM models
│   │   ├── schemas/            # Pydantic schemas
│   │   ├── services/           # Core business logic
│   │   │   ├── chat_agents.py          # Orchestration (JudgeAgent + routing)
│   │   │   ├── react_agent.py          # ReAct multi-step reasoning loop
│   │   │   ├── retrieval.py            # Hybrid RRF fusion retrieval
│   │   │   ├── retrieval_validator.py  # Retrieval quality gate
│   │   │   ├── query_rewriter.py       # Coreference resolution + decomposition
│   │   │   ├── reranker.py             # BGE cross-encoder reranker
│   │   │   ├── vector_store.py         # Milvus operations
│   │   │   ├── bm25_retriever.py       # BM25 keyword search
│   │   │   ├── sql_agent.py            # SQL intent detection & execution
│   │   │   ├── llm.py                  # LLM client (LangChain wrapper)
│   │   │   ├── llm_prompts.py          # Prompt management system
│   │   │   └── ...                     # Security, embedding, text splitting, etc.
│   │   └── templates/          # Prompt template files (Perplexity-style inline citations)
│   ├── scripts/                # Index building, evaluation, migration
│   └── tests/                  # Test suite + evaluation datasets (1070 QA pairs)
├── frontend/                   # React + Vite + Ant Design (Notion+Claude light theme)
├── sample_data/                # Excel templates for 3 domains
├── docs/                       # Architecture, study plans, skill roadmaps
└── docker-compose.milvus.yml   # Milvus standalone deployment
```

---

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- Docker Desktop (for Milvus)

### 1. Start Milvus

```bash
docker compose -f docker-compose.milvus.yml up -d
```

### 2. Setup Backend

```bash
cd backend
cp .env.example .env   # Edit with your DB/API credentials
pip install -r requirements.txt
uvicorn app.main:app --reload --app-dir backend
```

### 3. Setup Frontend

```bash
cd frontend
npm install
npm run dev
```

### 4. Build Search Index

```bash
# Full index rebuild from MySQL
python -m backend.scripts.sync_mysql_index

# Domain-specific
python -m backend.scripts.sync_mysql_index --domain tender
```

### 5. Run Evaluation (optional)

```bash
python -m backend.tests.test_retrieval_recall_eval
python -m backend.scripts.evaluate_multi_model  # Multi-model comparison
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **RRF over linear combination** | Better handles different score distributions across retrieval sources |
| **IVF_FLAT over HNSW** | Better recall-rebuild tradeoff for our corpus size (~50K chunks) |
| **Custom ReAct Agent** | Avoids LangChain Agent abstraction overhead; full control over tool definitions and loop logic |
| **Qwen tiered deployment** | turbo (rewrite/classify), plus (answer), max (offline eval) — cost-performance optimal |
| **Retrieval Validator** | Rule-based gate before LLM generation reduces hallucination from poor-quality chunks |

---

## Security

- SQL LIKE wildcard injection prevention (`escape_like()`)
- File upload type + size validation
- Role-based access control (admin/internal/supplier)
- Milvus authentication support
- CORS whitelist
- Password hashing with configurable iterations

---

## License

This project is for portfolio demonstration. Contact the author for usage terms.

---

*Built by [Riki2001-123](https://github.com/Riki2001-123) — internship project at iFLYTEK, 2026.*
