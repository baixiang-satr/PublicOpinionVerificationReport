# 舆情验证报告工具

本项目是一个 Windows 本地桌面工具：导入网页 URL，采集页面基础信息，并为每条记录输出内容页截图和可取得时的作者主页截图，最后生成固定格式的 `template.zip`。

T01 至 T08 已完成：模板契约、URL 导入、页面采集、平台路由、截图与附件、固定模板导出、端到端任务编排、桌面界面和发布检查均已接通并通过自动化验证。

## 交付契约

- [template/](template/) 是只读基准，程序绝不能修改其中任何文件。
- 每个任务复制基准目录到独立 staging 区，在副本中填充结果。
- 最终文件名固定为 `template.zip`，其内部保留 `template/` 顶层目录。
- `template.xlsx` 的工作表、表头、下拉值、格式和保护必须保持不变；截图和附件放在同一目录。
- Excel 中每一个非空截图/附件文件名必须对应 ZIP 内的实际文件。

固定模板为受保护的 Office OLE 工作簿，不能用 `openpyxl` 重建或另存。`openpyxl` 仅用于读取用户输入的普通 `.xlsx`；模板副本通过 `OoxmlTemplateWriter`（Office Open XML 直接写入）或 `ExcelTemplateWriter`（Excel COM 回退）写入并验证。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [requirements.md](requirements.md) | 业务范围、8 张工作表、字段映射、标准枚举和验收标准。 |
| [design.md](design.md) | 模块设计、数据模型、任务状态机、模板写入、GUI 和测试方案。 |
| [docs/template_contract.md](docs/template_contract.md) | 固定模板与 `template.zip` 的开发检查清单。 |
| [docs/reference_review.md](docs/reference_review.md) | 三个参考项目的取舍与本项目最终选择。 |
| [docs/task_breakdown.md](docs/task_breakdown.md) | 按依赖与验收标准拆分的实施任务。 |
| [docs/ai_coding_constraints.md](docs/ai_coding_constraints.md) | 编码前必须遵守的 10 条 AI 约束。 |
| [docs/developer_guide.md](docs/developer_guide.md) | 开发环境、模块职责、测试和完成定义。 |
| [docs/user_guide.md](docs/user_guide.md) | 完成实现后的安装、操作流程和限制说明。 |

## 运行工具

安装依赖和浏览器后启动桌面界面：

```powershell
pip install -r requirements.txt
python -m playwright install chromium
python -m src.main
```

界面按“选择 URL 文件、检查设置、开始生成”三步操作。成功后会显示 `output/<任务编号>/template.zip` 的绝对路径；同目录还会生成质量报告和待人工补录清单。取消或没有可映射记录时不会生成空包。

## 处理流程

```text
输入文件 -> URL 解析/去重 -> 任务编排 -> Playwright 页面访问
  -> 平台路由与字段提取 -> 内容/主页截图 -> 部分字段可导出校验
  -> OOXML / Excel COM 写入模板副本 -> 附件引用校验 -> template.zip
```

运行态会保留标题、作者主页 URL、状态码、重定向链和错误信息；其中模板没有对应列的字段只在 GUI 和本机运行日志中显示，不会擅自加入 ZIP。

所有网站统一优先提取公开的真实用户账号；页面只提供昵称时，账号字段回退为昵称并标记 `nickname_fallback`，后续若在已核验作者主页取得真实账号会自动升级。自动截图和人工框选共用横向版面校正：异常离屏元素把正文或主页推到视口外时，先将实质内容归位，再从视口原点截图，避免跨站横向偏移或二次裁切。

## 项目目录

```text
src/
├── auth/                   # 34 平台登录策略、游客探测、DPAPI 状态存储与复验
├── config/                 # 不可变模板配置与可覆盖任务配置
├── domain/                 # 任务、抓取结果、模板结构模型
├── input/                  # TXT / CSV / 普通 XLSX 的 URL 导入
├── crawler/                # 调度器、路由器、通用与平台提取器
├── screenshot/             # 浏览器池、页面/主页截图、临时 OCR 图片
├── export/                 # 模板副本、行映射、Excel COM、校验和打包
├── services/               # 端到端任务编排
├── tools/                  # 受限页诊断与可视人工接力
├── webui/                  # pywebview 桌面壳、js_api 桥与后台 worker
└── utils/                  # 文件、时间、日志和 URL 工具

web/                        # Vue 3 + Element Plus 前端（Univer 表格），npm run build 产出 dist

tests/
├── test_input/
├── test_crawler/
├── test_screenshot/
├── test_export/
├── test_services/
├── test_webui/
└── contract/               # 仅 Windows + Excel 环境运行的模板契约测试

tools/
└── release_check.py        # 代码、文档链接、模板哈希和 ZIP 发布检查
```

## 开发环境

前置条件：Windows 10/11、Python 3.11+、可自动化的 Microsoft Excel，以及 Playwright Chromium。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -m "not excel and not external"
```

真实模板契约测试使用 `python -m pytest -m excel tests/contract`。发布前运行 `python tools/release_check.py`；普通测试和发布检查均不会访问真实外部站点。

### 抖音视频专项验收工具

`tools/test_douyin_fix.py` 封装了两条真实抖音短链的定向验收：校验目标视频正文、可见发布时间、内容页截图和个人页截图，结果写入独立的 `output/test-douyin-*` 目录。它会访问真实页面并复用本机已保存的抖音登录态。

```powershell
# 两条都验收；跳过重复预检可减少触发平台风控
.venv\Scripts\python.exe -X utf8 tools\test_douyin_fix.py --headed --edge --skip-precheck

# 只验收第 1 或第 2 条
.venv\Scripts\python.exe -X utf8 tools\test_douyin_fix.py --headed --edge --skip-precheck --only 1
```

### 小红书笔记专项验收工具

`tools/test_xiaohongshu_fix.py` 使用游客优先的独立浏览器上下文，按 URL 中的笔记 ID 提取标题、正文、作者、账号和完整发布时间，并生成内容页截图及可取得的作者主页截图。默认 URL 是当前小红书回归样例；完整验收只请求笔记一次，以降低触发平台频控的概率。默认使用可视 Edge，因为小红书当前会对部分无头 Chromium 会话返回安全限制。

```powershell
# 使用默认回归 URL 完整验收
.venv\Scripts\python.exe -X utf8 tools\test_xiaohongshu_fix.py

# 只检查目标笔记内嵌数据，不截图
.venv\Scripts\python.exe -X utf8 tools\test_xiaohongshu_fix.py --precheck-only

# CI/无桌面环境可尝试无头模式；若出现 300012 请改回默认模式
.venv\Scripts\python.exe -X utf8 tools\test_xiaohongshu_fix.py --headless

# 仅在游客访问确实要求登录时，显式复用已保存的小红书登录态
.venv\Scripts\python.exe -X utf8 tools\test_xiaohongshu_fix.py --use-saved-login
```

## 打包分发（免安装）

把工具交给其他 Windows 电脑使用时，执行一键打包：

```powershell
cd web && npm run build && cd ..
python tools/build_release.py
```

产物 `dist/舆情验证报告工具/` 包含 exe、固定模板、前端、Playwright 浏览器与《使用说明.txt》，整包压缩拷贝即可，目标电脑无需安装 Python、Node.js 或浏览器（需 Win10/11 64 位与系统自带 WebView2）。

## 参考项目取舍

`references/MediaCrawler-main` 的任务生命周期、资源清理、浏览器上下文和平台适配器设计被吸收；`MediaCrawler-new-main` 提供了保持骨架精简的思路；浏览器插件帮助确认模板字段、示例行和截图附件关联。

登录态采用“游客优先、逐平台隔离”的方式管理：先对 34 个模板平台执行游客探测；只有明确要求登录或触发人工验证的平台，才由用户在平台官方页面完成手机号登录、验证码或扫码。候选会话必须在全新的 Playwright context 中复验成功，才会按平台写入 Windows 当前用户 DPAPI 加密存储。抓取时不同平台不共享 context；旧版综合 `storage_state` JSON 只作为兼容迁移来源。

批量抓取会先读取逐平台健康状态，并让同平台 URL 串行通过同一个登录态门禁。首条记录一旦确认登录失效、验证码或访问验证屏障，当前平台剩余 URL 会暂停并进入待补录/重试清单，其他平台继续并行处理。页面字段依次尝试平台 DOM、JSON-LD、Next/Nuxt 等内嵌状态、内容型 XHR/Fetch JSON、Meta 和可见文本；已知平台还可使用同一官方平台的保守 URL 变体。

手机号输入框只是可选的辅助填写，不会自动发送验证码；验证码、密码和扫码信息只在平台页面中处理。状态索引只记录脱敏手机号、验证时间和错误码，不保存明文 Cookie/Token，也不会进入日志或 `template.zip`。

## 合规边界

工具只处理用户有权处理的信息，不自动破解验证码，不绕过付费墙、登录权限或站点访问控制。`src/tools/page_access.py` 只负责识别受限页面、阻止错误截图，并支持用户在可视浏览器中手工处理。Cookie、令牌、浏览器 profile、调试 HTML 和本机日志均不得打入 `template.zip`。
