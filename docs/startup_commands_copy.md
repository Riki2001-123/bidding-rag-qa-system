# 项目启动命令（直接复制版）

项目路径：`D:\python\PythonProject\RAG+LLMProject`

说明：平时启动需要开两个 PowerShell 窗口，一个跑后端，一个跑前端。

## 1. 后端首次安装（只需要做一次）

```powershell
cd D:\python\PythonProject\RAG+LLMProject
pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

如果 `backend\.env` 已经存在，这一步以后就不用再执行。

## 2. 后端启动（每天复制这个）

```powershell
cd D:\python\PythonProject\RAG+LLMProject
uvicorn app.main:app --reload --app-dir backend
```

## 3. 前端首次安装（只需要做一次）

```powershell
cd D:\python\PythonProject\RAG+LLMProject\frontend
npm install
```

## 4. 前端启动（每天复制这个）

```powershell
cd D:\python\PythonProject\RAG+LLMProject\frontend
npm run dev
```

## 5. 最省事的复制方式

PowerShell 窗口 1：后端

```powershell
cd D:\python\PythonProject\RAG+LLMProject
uvicorn app.main:app --reload --app-dir backend
```

PowerShell 窗口 2：前端

```powershell
cd D:\python\PythonProject\RAG+LLMProject\frontend
npm run dev
```

## 6. 启动后访问地址

- 前端：`http://127.0.0.1:5173`
- 后端接口文档：`http://127.0.0.1:8000/docs`

## 7. 提醒

如果要调用大模型，请先检查 `backend\.env` 里的 `OPENAI_API_KEY` 等配置是否已经填写。
