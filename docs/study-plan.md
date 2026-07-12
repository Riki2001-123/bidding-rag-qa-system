# 招投标采购智能问答系统 2 周学习计划

这份计划面向“能独立跑通、能解释主链路、能做小改动、能排查常见问题”的目标设计，默认每天投入 2 小时。  
主线不是零散学框架，而是吃透这条链路：

`登录鉴权 -> Excel 导入 -> 数据入库 -> 文本切块 -> Embedding -> BM25/向量混合检索 -> LLM 回答 -> 前端联调`

建议搭配文档一起使用：

- `docs/system-study-map.md`：先建立全局认知
- `docs/study-execution-guide.md`：每天按清单执行
- `docs/maintenance-lab.md`：第 2 周排错与联调
- `docs/skill-workbook.md`：记录每天的产出和复盘

## 学习方式

每天都按同一节奏推进：

1. 先看指定源码，建立当天主题的模块认知。
2. 再运行页面、接口或测试，验证自己理解的是对的。
3. 最后写笔记、复述链路、提交一个固定产出。

## 核心技能

| 能力方向 | 要掌握的内容 | 在当前项目里的对应位置 |
| --- | --- | --- |
| Python 后端基础 | FastAPI 路由组织、依赖注入、Schema、服务层拆分 | `backend/app/main.py`、`backend/app/api/*`、`backend/app/schemas/*` |
| 数据建模与持久化 | SQLAlchemy 模型、业务表职责、查询过滤 | `backend/app/models/entities.py`、`backend/app/db/*` |
| Excel 导入与清洗 | `pandas`、字段映射、必填校验、结构化与语义字段分流 | `backend/app/services/excel_import.py` |
| RAG 主流程 | 切块、Embedding、向量库、BM25、混合召回 | `backend/app/services/text_splitter.py`、`embeddings.py`、`vector_store.py`、`retrieval.py` |
| LLM 问答链路 | 问题如何转成检索、证据如何传给模型、回答与引用如何返回 | `backend/app/services/chat.py`、`llm.py` |
| 前端联调 | React 页面结构、请求封装、token 透传、接口联调 | `frontend/src/pages/*`、`frontend/src/api/client.js` |
| 调试与验证 | smoke test、接口排错、权限问题定位、导入与检索异常排查 | `backend/tests/smoke_test.py`、`docs/maintenance-lab.md` |

## 学前准备

### 环境准备

后端：

```bash
pip install -r backend/requirements.txt
copy backend\.env.example backend\.env
uvicorn app.main:app --reload --app-dir backend
```

前端：

```bash
cd frontend
npm install
npm run dev
```

测试：

```bash
cd backend
python -m unittest tests.smoke_test
```

### 学习素材

- 默认账号：`admin / admin123`、`internal / internal123`、`supplier / supplier123`
- 样例数据：`sample_data/policy_sample.xlsx`、`sample_data/tender_sample.xlsx`、`sample_data/enterprise_sample.xlsx`

## 两周安排

| 天数 | 学习主题 | 重点源码 | 运行/验证动作 | 当天固定产出 |
| --- | --- | --- | --- | --- |
| Day 1 | 项目目录、启动方式、前后端入口 | `README.md`、`backend/app/main.py`、`frontend/src/main.jsx` | 启动前后端，打开登录页 | 画出前后端启动入口图 |
| Day 2 | API 路由和页面路由 | `backend/app/api/router.py`、`frontend/src/App.jsx`、`frontend/src/layouts/LayoutShell.jsx` | 登录后点一遍主菜单 | 口述“登录到问答”的页面与接口路径 |
| Day 3 | 数据模型和业务域 | `backend/app/models/entities.py` | 结合样例 Excel 对照字段 | 写表格解释三类业务域适合回答什么问题 |
| Day 4 | 权限与鉴权 | `backend/app/api/deps.py`、`backend/app/services/security.py`、`backend/app/services/retrieval.py` | 分别用 3 个默认账号登录并观察 | 写出 3 个“为什么不同角色看到结果不同”的例子 |
| Day 5 | Excel 导入链路 | `backend/app/api/routers/imports.py`、`backend/app/services/excel_import.py` | 下载模板并导入 1 份样例 Excel | 画出“Excel 上传后发生了什么”的顺序图 |
| Day 6 | 切块、Embedding、向量索引 | `backend/app/services/text_splitter.py`、`backend/app/services/embeddings.py`、`backend/app/services/vector_store.py` | 导入前后观察 `storage/vector_store` 变化 | 用自己的话解释“为什么导入后能被语义检索到” |
| Day 7 | 检索与问答链路 | `backend/app/api/routers/search.py`、`backend/app/api/routers/chat.py`、`backend/app/services/chat.py`、`backend/app/services/llm.py` | 同一数据分别做 `search` 与 `chat` | 对比两条链路的输入、处理与输出 |
| Day 8 | 登录页和首页 | `frontend/src/pages/LoginPage.jsx`、`frontend/src/pages/DashboardPage.jsx` | 登录并刷新页面，确认 token 仍生效 | 说明前端如何把登录态带到后续接口 |
| Day 9 | 导入页、搜索页、问答页 | `frontend/src/pages/ImportPage.jsx`、`frontend/src/pages/SearchPage.jsx`、`frontend/src/pages/ChatPage.jsx`、`frontend/src/api/client.js` | 在三页分别触发一次真实请求 | 画出“页面 -> API -> 服务 -> 数据表”映射关系 |
| Day 10 | 系统联调 | 前后端全流程 | 从零完成一次登录、导入、搜索、问答 | 记录一份完整操作笔记 |
| Day 11 | 测试和排错 | `backend/tests/smoke_test.py`、`docs/maintenance-lab.md` | 跑通 smoke test 并模拟 3 类故障定位 | 写出至少 3 类故障的排查步骤 |
| Day 12 | 小改动练习 1 | 搜索页、搜索接口 | 增加一个搜索筛选项并验证可用 | 记录需求、改动点和验证结果 |
| Day 13 | 小改动练习 2 | Excel 导入配置 | 调整一个字段映射或模板字段并重新导入 | 写一份小变更说明 |
| Day 14 | 复盘和讲解 | 全项目 | 脱离代码做一次系统讲解 | 完成一版 10 分钟讲解稿 |

## 每天 2 小时模板

| 时间 | 动作 | 说明 |
| --- | --- | --- |
| 30 分钟 | 读源码 | 只看当天主题相关文件，避免同时打开太多模块 |
| 40 分钟 | 跑页面/接口/测试 | 用真实现象验证理解，不只停留在代码阅读 |
| 30 分钟 | 写笔记 | 记录“今天学会了什么”“还有什么没搞懂” |
| 20 分钟 | 脱稿复述 | 不看代码直接讲一遍主链路 |

## 每周验收

### 第 1 周结束

- 能解释项目入口、路由组织方式和三类业务域。
- 能说清 Excel 导入、切块、向量化、检索、问答的先后顺序。
- 能说明权限过滤为什么会影响搜索结果和附件可见性。xi

### 第 2 周结束

- 能独立启动前后端并完成一次导入、搜索、问答。
- 能在前端页面、后端接口、服务层、数据表之间建立对应关系。
- 能完成一次小改动并确认主流程没有被破坏。
- 能做一次 10 分钟系统讲解。

## 验收与测试场景

1. 启动验证：独立启动前后端，登录默认账号并进入系统。
2. 导入验证：解释 Excel 导入后的步骤，并成功导入至少 1 份测试数据。
3. 检索验证：说明结构化检索和语义检索的区别，并解释某条结果为什么被召回。
4. 问答验证：描述问答接口如何依赖检索结果，并完成一次有引用依据的问答。
5. 权限验证：解释不同角色为什么看到不同结果，并能定位权限过滤相关问题。
6. 改动验证：完成 1 个小需求，不破坏导入、搜索、问答主流程。

## 重点掌握的公开接口

- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/imports/{domain}/excel`
- `GET /api/search/{domain}`
- `POST /api/chat/query`
- `POST /api/attachments/upload`

理解重点：

- 前端 `apiFetch()` 统一处理 token 和错误。
- 后端 router 负责收参与返回，service 负责核心业务逻辑。
- `policy`、`tender`、`enterprise` 是三类固定业务域。

## 默认假设

- 默认你每天可投入约 2 小时。
- 默认你的目标是接手并维护当前项目，而不是立即做底层重构或性能调优。
- 默认优先级是先跑通和讲清主链路，再做局部优化。
- 默认学习材料以仓库现有文档和源码为主。

## 配套文档

- [14 天执行清单](./study-execution-guide.md)
- [系统学习地图](./system-study-map.md)
- [联调与排错手册](./maintenance-lab.md)
- [图中技能 4 周路线图](./skill-roadmap-4weeks.md)
- [技能学习产出模板](./skill-workbook.md)
