# GitHub Pages 展示发布 V1 — EVALUATE

日期：2026-10-06。依据 [SPEC](pages-deployment-spec.md)。用户已授权 GitHub Pages 公开发布。

## 本机通过

- 前端 5 项单元检查通过；普通生产构建和 pages 独立构建通过。普通构建保留真实登录和业务页，公开构建只包含展示、演示与接入说明。
- 公开产物检查通过：只包含静态资源及入口文件，没有真实业务组件、后端地址、聊天历史字段、密钥、数据库、日志或测试截图。Actions 只上传 dist/pages-deployment/site。
- 无 SPA 回退的模拟 Pages 静态服务器上，9 项浏览器检查通过：仓库子目录、资源与链接、案例参数及刷新、中英文四案例及追问、未匹配问题、搜索/筛选/空结果、键盘引用、所有真实入口说明页、手机布局和私有状态隔离。请求及资源错误为零，业务 API 请求为零。
- 普通构建 16 项浏览器回归通过，包含匿名业务入口、受控登录、SSE、附件鉴权、错误、取消和检索协议。受控响应不代表再次真实后端联调，本版后端未修改。
- 工作流语法、master 分支及上传范围检查通过；代码与新增文件未检出本机配置密钥。
- 桌面首页与手机演示截图已查看，样例标识与接入说明可见，横向布局正常。

## 初次失败及修复

- 首页源码链接含图标，严格匹配完整可访问名称导致测试未命中；改为文字定位，目标链接核验通过。
- 旧普通构建测试的 Search 按钮同样受图标影响，改为定位搜索表单的 submit 按钮。
- 旧会话清空检查保留了 ?case=association，刷新会按参数重新打开预设案例。改为无 case 参数的路径测试内存会话清空；带参数的直达与刷新由 Pages 测试单独验证。业务行为未因此修改。

## 真实公网验收

- GitHub Pages 已通过 Actions 发布，公开地址为 [展示首页](https://riki2001-123.github.io/bidding-rag-qa-system/) 和 [免登录演示](https://riki2001-123.github.io/bidding-rag-qa-system/demo/)。
- 发布代码版本为 `dbf16276c1f972d1cecfeffadababe496c8d70b4`；[Actions 运行 37481149467](https://github.com/Riki2001-123/bidding-rag-qa-system/actions/runs/37481149467) 的构建、安装及验证、产物上传与部署均成功。
- 对实际 HTTPS 地址执行同一套浏览器验收，9/9 通过：子目录首页及主要链接、带参数的演示直达与刷新、中英四案例及追问、未知问题、检索筛选与空结果、键盘引用、四个真实系统入口说明页、手机布局及状态隔离。已知入口最终响应为 200；业务 API 请求、资源错误、脚本错误均为零，预置私有登录和聊天状态没有被读取或修改。
- 公网桌面首页、手机演示与引用抽屉截图已保存并查看，样例标识可见，没有横向溢出。报告为 `dist/pages-deployment/public-report.json`，截图为 `public-home.png`、`public-demo-mobile.png` 和 `public-source-mobile.png`。
- 本机与公网当前验收项全部通过，没有遗留失败项。公网验证使用本机 Edge 桌面与手机视口；真实手机设备和其他地区网络未验证。

## 边界与证据

- 发布的是虚构资料和预设问答，不调用真实模型。真实服务、数据库和本机账号没有部署；公开版相关入口显示接入说明，没有登录表单。
- 使用仓库子目录静态入口，已知页面无需服务器 SPA 回退；未知 URL 的 404.html 仅引导返回首页，不能将未知页面状态当作已知路由成功。
- 本机报告和截图位于被 Git 忽略的 dist/pages-deployment/：local-report.json、local-initial-report.json、normal-browser-report.json、normal-browser-initial.json、normal-browser-query-initial.json、frontend-unit.txt、normal-build.txt、build.txt 和 PNG。Pages 构建单独位于 site/。
- 自定义域名、真实业务部署、商业视频、全区域访问稳定性及负载测试未验证，留待下一版规格。
