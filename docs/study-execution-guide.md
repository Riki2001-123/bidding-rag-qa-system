# 14 天执行清单

这份清单是 `docs/study-plan.md` 的执行版。  
用法很简单：每天只做当天主题，不跳读，不同时追太多模块；看完源码就去跑接口或页面，最后留下一份可复盘的产出。

## Day 1 项目结构与启动入口

学习目标：
搞清楚前后端分别从哪里启动，项目启动时自动做了什么。

阅读文件：

- `README.md`
- `backend/app/main.py`
- `backend/app/core/config.py`
- `frontend/src/main.jsx`

运行动作：

```bash
uvicorn app.main:app --reload --app-dir backend
```

```bash
cd frontend
npm run dev
```

当天要回答的问题：

- FastAPI 是如何注册路由的？
- 前端 React 应用从哪个入口挂载？
- 后端启动时为什么会自动初始化数据库？

当天产出：

- 一张“前后端启动入口图”

## Day 2 路由总览与页面路径

学习目标：
把“接口入口”和“页面入口”建立一一对应关系。

阅读文件：

- `backend/app/api/router.py`
- `frontend/src/App.jsx`
- `frontend/src/layouts/LayoutShell.jsx`

运行动作：

- 登录后点击“总览 / Excel 导入 / 检索 / 问答”四个页面
- 在浏览器里观察 URL 变化

当天要回答的问题：

- 后端为什么要有一个总路由文件？
- 前端未登录时如何跳回 `/login`？
- 登录成功后页面容器是谁负责渲染的？

当天产出：

- 一段 3 分钟口述稿：“用户从登录进入问答页，中间经过了哪些页面和接口”

## Day 3 数据模型与三类业务域

学习目标：
看懂 `policy`、`tender`、`enterprise` 三类数据的职责和差异。

阅读文件：

- `backend/app/models/entities.py`
- `docs/system-study-map.md`

辅助材料：

- `sample_data/policy_sample.xlsx`
- `sample_data/tender_sample.xlsx`
- `sample_data/enterprise_sample.xlsx`

当天要回答的问题：

- 三类业务域各自的关键结构化字段是什么？
- `attachments`、`text_chunks`、`chat_sessions` 分别服务哪条链路？
- 为什么不是所有业务字段都适合做结构化过滤？

当天产出：

- 一页表格：三类业务域适合回答的问题、关键字段、语义字段

## Day 4 权限与鉴权

学习目标：
理解权限过滤为什么会同时影响搜索结果和附件可见性。

阅读文件：

- `backend/app/api/deps.py`
- `backend/app/services/security.py`
- `backend/app/services/retrieval.py`

运行动作：

- 分别使用 `admin`、`internal`、`supplier` 登录
- 对同一关键词做搜索并记录差异

当天要回答的问题：

- token 在哪里生成和校验？
- `apply_permission_filters()` 在哪几条链路里复用？
- 没有项目授权时为什么只能看到 `project_id is null` 的数据？

当天产出：

- 3 个例子：同一条数据为什么不同角色看到的结果不同

## Day 5 Excel 导入链路

学习目标：
看懂一份 Excel 是如何从文件变成数据库记录和 TextChunk 的。

阅读文件：

- `backend/app/api/routers/imports.py`
- `backend/app/services/excel_import.py`

运行动作：

- 下载一个模板
- 导入 `sample_data` 中任意一份样例 Excel

当天要回答的问题：

- 列名别名映射在哪里定义？
- 必填字段校验发生在什么阶段？
- 为什么结构化字段和语义字段要分开处理？

当天产出：

- 一张“Excel 上传后发生了什么”的顺序图

## Day 6 切块、Embedding、向量索引

学习目标：
理解“为什么导入后能被语义检索到”。

阅读文件：

- `backend/app/services/text_splitter.py`
- `backend/app/services/embeddings.py`
- `backend/app/services/vector_store.py`
- `docs/architecture.md`

运行动作：

- 导入前后观察 `storage/vector_store` 目录下的文件变化

当天要回答的问题：

- 为什么不是整行记录直接入向量库？
- `max_chars` 和 `overlap` 分别解决什么问题？
- 没有在线 embedding 模型时系统是如何降级的？

当天产出：

- 一段自己的解释：“一条 Excel 数据为什么后续会被语义检索命中”

## Day 7 检索与问答主链路

学习目标：
把 `search` 和 `chat` 两条链路分清楚。

阅读文件：

- `backend/app/api/routers/search.py`
- `backend/app/api/routers/chat.py`
- `backend/app/services/retrieval.py`
- `backend/app/services/chat.py`
- `backend/app/services/llm.py`

运行动作：

- 对同一业务域先搜索，再问答

当天要回答的问题：

- `search` 返回的是什么？
- `chat` 相比 `search` 多做了什么？
- citation 为什么不是前端临时拼出来的？

当天产出：

- 一页对比表：`search` vs `chat`

## Day 8 登录页和首页

学习目标：
搞懂前端登录态、token 保存和首页加载。

阅读文件：

- `frontend/src/pages/LoginPage.jsx`
- `frontend/src/pages/DashboardPage.jsx`
- `frontend/src/api/client.js`

运行动作：

- 登录后刷新页面
- 删除浏览器 localStorage 中的 token 再访问主页

当天要回答的问题：

- token 保存在哪里？
- 未登录访问首页为什么会被重定向？
- 页面加载时如何拿到当前用户信息？

当天产出：

- 一段说明：前端怎样把登录态带到后续所有接口

## Day 9 导入页、搜索页、问答页

学习目标：
把“页面 -> API -> 服务 -> 数据表”真正连起来。

阅读文件：

- `frontend/src/pages/ImportPage.jsx`
- `frontend/src/pages/SearchPage.jsx`
- `frontend/src/pages/ChatPage.jsx`
- `frontend/src/api/client.js`

运行动作：

- 在三页各触发一次真实请求并记录请求目的

当天要回答的问题：

- 每个页面分别调用了哪些接口？
- 查询参数是谁组装的？
- 返回的数据最终展示在页面哪块区域？

当天产出：

- 一张映射表：页面 -> API -> 服务 -> 数据表

## Day 10 全流程联调

学习目标：
独立从零跑完一次完整链路。

运行动作：

1. 启动前后端
2. 登录
3. 导入一份 Excel
4. 搜索刚导入的数据
5. 在问答页提问

当天要回答的问题：

- 导入成功后如何验证数据真的可检索？
- 如果搜索有结果但问答回答很弱，优先排查哪一层？

当天产出：

- 一份完整操作笔记或录屏说明

## Day 11 测试与排错

学习目标：
从“会用”过渡到“会定位问题”。

阅读文件：

- `backend/tests/smoke_test.py`
- `docs/maintenance-lab.md`

运行动作：

```bash
cd backend
python -m unittest tests.smoke_test
```

当天要回答的问题：

- smoke test 覆盖了哪些最小可用流程？
- 导入失败、搜索为空、问答无引用，各优先排查哪里？
- 为什么问答保守不一定是 bug？

当天产出：

- 至少 3 类常见故障的排查步骤

## Day 12 小改动练习 1

学习目标：
完成一个不破坏主流程的小需求。

推荐练习：

- 给 `policy` 或 `enterprise` 搜索补一个筛选项

建议改动链路：

1. 页面增加控件
2. 请求拼接参数
3. 后端路由接收参数
4. 服务层补过滤条件

当天产出：

- 一份小改动记录：需求、改动点、验证结果

## Day 13 小改动练习 2

学习目标：
理解导入配置改动的影响范围。

推荐练习：

- 在 Excel 导入配置里补一个中文列名别名
- 或调整一个模板字段的展示说明

建议验证：

1. 重新导入对应 Excel
2. 确认数据成功入库
3. 搜索或问答验证字段有效

当天产出：

- 一份变更说明：改了哪里、为什么不影响主链路

## Day 14 复盘和讲解

学习目标：
把零散理解变成完整表达。

当天要完成的动作：

- 脱离代码讲一次系统
- 对照 `docs/skill-workbook.md` 做最后复盘

讲解建议顺序：

1. 系统解决什么问题
2. 输入数据是什么
3. 导入、切块、检索、问答的主链路
4. 权限如何影响结果可见性
5. 前端页面如何承接这些能力
6. 后续还能怎么扩展

当天产出：

- 一版 10 分钟系统讲解稿

## 每天结束前的固定检查

- 我今天是否真的运行了页面、接口或测试，而不只是看代码？
- 我今天是否留下了文字、图表或录屏产出？
- 我能否在不看代码的情况下讲清今天的主链路？
- 我今天遇到的问题，是否记录到了 `docs/skill-workbook.md`？
