# Showcase V1 — 本地、托管与测试

## GitHub Pages 公开版

首页：https://riki2001-123.github.io/bidding-rag-qa-system/；演示：https://riki2001-123.github.io/bidding-rag-qa-system/demo/。

GitHub Pages 使用 frontend 下的 npm run build:pages，输出 dist/pages-deployment/site；公开路由使用仓库子目录 base，/demo 与真实系统说明页各生成目录 index.html，支持直接访问及刷新。只有此 site 目录上传，整个 dist、数据库、密钥和运行产物不上传。真实登录及业务组件不进入此模式，公开版 API 客户端禁止发出业务请求。

.github/workflows/pages.yml 在 master 的前端或工作流改动时构建部署，也支持 Actions 手动触发。仓库 Settings → Pages 的 Source 为 GitHub Actions。默认仓库名改变或使用自定义域名时，先调整 VITE_SITE_BASE 和发布规格再构建。以官方 [Vite 静态部署](https://v6.vite.dev/guide/static-deploy) 与 [GitHub Pages Actions](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) 为配置依据。

Pages 浏览器验收：本机 D 盘 TEMP/TMP，PLAYWRIGHT_MODULE_DIR 指向已有 Playwright，frontend 下运行 npm run test:pages 默认使用无 SPA 回退的静态服务器模拟；设置 PAGES_TEST_URL=https://riki2001-123.github.io/bidding-rag-qa-system/ 后验证实际站点。报告及截图位于 dist/pages-deployment/。发布规格见 [pages-deployment-spec.md](pages-deployment-spec.md)。

## 本地预览

在项目根目录设置 D 盘缓存和临时目录（参见双语 README），进入 frontend：

```powershell
npm run build
npm run preview -- --host 127.0.0.1 --port 4173 --strictPort
```

普通构建输出为根目录 dist/showcase，供包含真实系统的独立部署使用。不要发布项目根目录、.env、数据库、原始业务数据或整个 dist（其中包含测试产物）。上文的 Pages 公开版使用独立构建目录。

## 静态托管 / Static hosting

使用域名根目录；部署 dist/showcase 中的 index.html 与 assets。浏览器直接访问 /demo、/login 时，服务器返回 index.html；存在的文件仍直接返回。可采用 Nginx：

```nginx
location / {
    try_files $uri $uri/ /index.html;
}
```

同域真实后端应有独立 /api/ 反向代理，不经过上述 SPA 回退。使用其他后端地址时，在 frontend/.env.local 中设置 VITE_API_BASE_URL 后重新构建，并配置后端 CORS。生产 API 使用可公开访问的 HTTPS 地址；127.0.0.1 指向每位访客自己的机器，仅适合本地开发。

The normal full-system build expects deployment at a domain root. Serve existing assets and rewrite page requests to index.html. Route /api/ separately when using a live backend. Public demo routes do not need a backend. The separate Pages build supports the repository subdirectory and publishes only public routes.

云端数据使用明确挂载的持久化目录；不要把 Windows 盘符用于 Linux 容器。现有 Milvus Compose 与后端数据迁移不属于本版执行范围。

## 单元与浏览器测试

单元测试不需要额外依赖：

```powershell
npm test
```

浏览器测试需要 Playwright Node 包和已安装的 Microsoft Edge。当前会话使用既有工具运行时里的 Playwright，只读取既有 C 盘依赖；浏览器临时文件和截图通过 TEMP/TMP 指定到 D 盘，不安装或下载浏览器。

如本地没有 Playwright，可以在 frontend 的 D 盘 node_modules 临时安装：

```powershell
npm install --no-save --package-lock=false playwright --cache ../.npm-cache
```

在另一个终端保留生产预览，然后执行：

```powershell
$projectPath = 'D:\python\PythonProject\RAG+LLMProject'
Set-Location (Join-Path $projectPath 'frontend')
New-Item -ItemType Directory -Force -Path ../dist/tmp | Out-Null
$env:TEMP = Join-Path $projectPath 'dist/tmp'
$env:TMP = $env:TEMP
$env:npm_config_cache = Join-Path $projectPath '.npm-cache'
npm run test:browser
```

如果使用外部既有 Node 包目录，设置 PLAYWRIGHT_MODULE_DIR 为包含 playwright 的 node_modules 绝对路径。SHOWCASE_TEST_URL 默认 http://127.0.0.1:4173；SHOWCASE_BROWSER 默认 msedge，可设为其他已安装且受 Playwright 支持的浏览器 channel。没有可用浏览器时记录未验证，不自动下载到默认目录。

Browser tests use a production preview, Playwright and an installed Edge channel. Optional environment variables are PLAYWRIGHT_MODULE_DIR, SHOWCASE_TEST_URL and SHOWCASE_BROWSER. Set TEMP/TMP to the D: project output directory before running.

测试结果写入 dist/showcase-tests/browser-report.json；截图、测试下载同在此目录。测试用临时 HTTP SSE 服务只模拟前端协议，不运行业务后端。所有测试上下文与测试 HTTP 服务在结束时关闭。

选定且目视检查的截图复制到 docs/images 作为 README 资源。测试输出仍保持 Git 忽略。
