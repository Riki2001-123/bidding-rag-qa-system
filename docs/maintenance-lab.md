# 联调与排错手册

这份手册对应学习计划的第 2 周，目标是帮你从“看懂”过渡到“会维护”。

## 1. 联调顺序

### 后端启动

```bash
pip install -r backend/requirements.txt
copy backend\.env.example backend\.env
uvicorn app.main:app --reload --app-dir backend
```

### 前端启动

```bash
cd frontend
npm install
npm run dev
```

### 首次验证

1. 用 `admin / admin123` 登录。
2. 到导入页下载模板并导入一份 Excel。
3. 到搜索页检索刚导入的数据。
4. 到问答页针对相同业务域提问。

## 2. 建议的实操任务

### 任务 A：跑通全流程

目标：
从空数据库开始完成一次导入、检索、问答，确认系统主链路可用。

检查点：

- 能看到默认账号登录成功。
- 导入返回 `completed` 或 `partial_success`。
- 搜索能返回至少一条记录。
- 问答能返回答案，并带引用标题。

### 任务 B：验证权限差异

目标：
对比 `admin`、`internal`、`supplier` 对同一批数据的可见范围。

建议做法：

1. 导入一批 `public` 数据。
2. 再导入一批 `internal` 数据。
3. 分别用 3 个账号搜索相同关键词。
4. 观察搜索结果和附件下载的差异。

关键源码：

- `backend/app/api/deps.py`
- `backend/app/services/retrieval.py`
- `backend/app/api/routers/attachments.py`

### 任务 C：确认 RAG 不是“纯大模型直答”

目标：
明确问答能力依赖检索证据，而不是脱离数据乱答。

建议做法：

1. 在搜索页搜一个明确存在的关键词。
2. 在问答页问同一业务域下的相关问题。
3. 再问一个数据中不存在的问题。
4. 对比有证据和无证据时的回答差异。

关键源码：

- `backend/app/services/chat.py`
- `backend/app/services/llm.py`

### 任务 D：观察向量文件变化

目标：
把“导入 -> 切块 -> 向量索引”这件事看成真实文件变化，而不只是抽象概念。

建议观察：

- `storage/vector_store/*.json`
- `storage/vector_store/*.npy`

导入新数据前后，你应该能看到对应业务域的向量索引文件发生变化。

## 3. 常见问题定位表

| 现象 | 优先排查位置 | 常见原因 |
| --- | --- | --- |
| 登录失败 | `auth.py`、`security.py`、初始化数据 | 用户名密码错误，数据库未初始化 |
| 导入报缺少字段 | `excel_import.py` | Excel 列名与别名映射不匹配 |
| 搜索为空 | `search.py`、`retrieval.py` | 权限过滤后无数据，或关键词不匹配 |
| 问答没有引用 | `chat.py`、`retrieval.py` | 检索未命中，或返回结果为空 |
| 问答内容很保守 | `llm.py` | 未配置在线模型，走了 fallback；或证据不足 |
| 附件下载失败 | `attachments.py` | 无权限访问，或存储路径不存在 |
| 语义检索效果弱 | `embeddings.py`、`vector_store.py` | 未配置 embedding 模型，当前走 hashing 降级 |

## 4. 小改动练习建议

### 练习 1：新增一个搜索筛选条件

推荐目标：
给 `policy` 或 `enterprise` 页面再补一个已有字段筛选项。

你会经过的链路：

1. 前端页面增加输入控件。
2. 拼接查询参数。
3. 后端路由读取参数。
4. `retrieval.py` 增加对应过滤条件。

### 练习 2：调整一个 Excel 字段映射

推荐目标：
在 `DOMAIN_CONFIGS` 中补充一个中文列名别名。

你会经过的链路：

1. 修改别名映射。
2. 重新导入 Excel。
3. 确认数据成功入库并可被搜索。

### 练习 3：修改前端展示字段

推荐目标：
在搜索页的表格里额外展示一个 `key_fields` 中的信息。

你会经过的链路：

1. 确认接口已返回该字段。
2. 修改 `SearchPage.jsx` 表格列。
3. 验证不同业务域下的展示效果。

## 5. 自测清单

- 我能说清 `search` 和 `chat` 的差别。
- 我知道 citation 不是前端拼出来的，而是后端问答时一起返回的。
- 我知道权限过滤在服务层统一处理，而不是散落在每个页面里。
- 我知道 embedding 模型缺失时系统为什么还能运行。
- 我知道新增一个字段筛选通常要改前端、路由和服务层三处。

## 6. 最后一次综合演练

如果你要确认自己已经达到“能维护 + 能讲解”，请独立完成下面动作：

1. 从零启动前后端。
2. 导入一份 `tender` 数据。
3. 用 `admin` 和 `supplier` 分别做一次检索。
4. 在问答页提问并解释引用是怎么来的。
5. 说出如果要新增一个过滤条件，你会改哪些地方。
