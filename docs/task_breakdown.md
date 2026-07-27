# 开发任务拆分

本任务清单以 [requirements.md](../requirements.md)、[design.md](../design.md) 和 [template_contract.md](template_contract.md) 为准。每项任务完成后应独立验证，不把未完成的下游功能当作验收前提。

## 当前状态（2026-07-28）

- [x] T01：模板配置、8 张工作表契约、标准枚举、源目录哈希清单与只读 Excel 契约校验已实现。
- [x] T02：运行态模型，以及 TXT、CSV、普通 XLSX 的 URL 导入、规范化、稳定去重和证据编号已实现。
- [x] T03：staging 模板副本、隔离 Excel COM 写入、资产引用校验和 `template.zip` 打包已实现。
- [x] T04：Playwright 生命周期、隔离 context、域名限速、有限重试、取消、状态码、重定向链与主截图已实现。
- [x] T05：完整平台目录、模板路由、平台 DOM 优先与 JSON-LD/meta/通用 DOM 回退、时间解析及 ID 昵称回退审计已实现。
- [x] T06：同一浏览器 context 内的作者主页截图、页面图片受控下载、真实格式校验、安全命名和非阻断错误已实现。
- [ ] T07 至 T08：待后续实现。

## T01 模板契约与配置基线

**目标**：把不可变模板、8 张工作表、列映射、标准枚举和任务配置固化为单一事实来源。

**涉及模块**：`src/domain/template_schema.py`、`src/config/settings.py`、`src/utils/file_utils.py`。

**完成标准**：可读取并校验源模板的文件清单、工作表顺序、首行字段、下拉值和保护状态；配置能区分不可改的模板项与可由 GUI 覆盖的任务项。

## T02 领域模型与输入处理

**目标**：建立稳定的任务、结果、路由、资产和错误模型，并完成 URL 文件导入。

**涉及模块**：`src/domain/models.py`、`src/input/reader.py`、`src/input/url_parser.py`、`src/utils/time_utils.py`。

**完成标准**：TXT、CSV、普通 XLSX 可提取 URL；URL 规范化、稳定去重和无效项统计正确；每条 URL 具有固定全局证据编号。

## T03 固定模板副本与 Excel 导出链路

**目标**：在不抓取网页的情况下，使用构造结果安全生成 `template.zip`。

**涉及模块**：`src/export/template_manager.py`、`src/export/row_mapper.py`、`src/export/excel_writer.py`、`src/export/package_validator.py`、`src/export/packager.py`。

**依赖**：T01、T02。

**完成标准**：源 `template/` 哈希不变；Excel COM 仅写 staging 副本；模板格式和保护保留；ZIP 包含 `template/` 顶层目录，且所有 Excel 附件引用真实存在。

## T04 浏览器生命周期与页面主截图

**目标**：建立可取消、可清理的 Playwright 浏览器池，完成单 URL 页面访问、状态码记录和主截图。

**涉及模块**：`src/screenshot/browser.py`、`src/screenshot/page_shooter.py`、`src/crawler/engine.py`。

**依赖**：T02。

**完成标准**：支持并发、超时、有限重试、域名限速和取消；记录原始/最终 URL 与状态码；生成稳定命名的主截图；任务结束后浏览器资源被释放。

## T05 通用解析、平台路由与首批平台适配器

**目标**：从渲染后的页面提取标题、正文、作者、账号、主页、发布时间和图片 URL，并映射至允许的模板工作表。

**涉及模块**：`src/crawler/content_parser.py`、`src/crawler/author_extractor.py`、`src/crawler/platform_router.py`、`src/crawler/extractors/`。

**依赖**：T01、T04。

**完成标准**：通用提取器有 JSON-LD、meta、DOM 回退；未匹配平台进入待人工补录；首批优先平台以 fixture 测试通过；不得伪造账号或平台枚举。

## T06 作者主页与页面图片附件

**目标**：补齐作者主页截图和受控的页面图片下载，并把真实文件名写入附件列。

**涉及模块**：`src/screenshot/author_shooter.py`、`src/screenshot/asset_collector.py`、`src/crawler/fetcher.py`。

**依赖**：T04、T05。

**完成标准**：主页失败不阻断主记录；图片按数量、大小和 MIME 类型过滤；所有输出文件使用安全、唯一名称；失败附件绝不写入 Excel。

## T07 端到端任务编排与桌面界面

**目标**：将输入、采集、资产、模板导出与打包串为一个可观察、可取消的任务。

**涉及模块**：`src/services/task_runner.py`、`src/ui/workers/task_worker.py`、`src/ui/main_window.py`、`src/ui/widgets/`、`src/main.py`。

**依赖**：T03 至 T06。

**完成标准**：GUI 主线程不阻塞；展示实时进度、日志和运行态审计字段；支持取消和失败项重试；成功时展示 `template.zip`，全失败时不生成空包。

## T08 自动化测试、模板契约与发布检查

**目标**：覆盖核心逻辑并形成每次交付前的固定检查。

**涉及模块**：`tests/test_input/`、`tests/test_crawler/`、`tests/test_screenshot/`、`tests/test_export/`、`tests/contract/`。

**依赖**：T01 至 T07。

**完成标准**：普通单元测试不访问外网；Playwright 使用本地/拦截 fixture；Windows + Excel 环境执行模板契约测试；通过代码风格、文档链接、源模板哈希和 ZIP 清单检查。

## 建议执行顺序

`T01 -> T02 -> T03 -> T04 -> T05 -> T06 -> T07 -> T08`。

T03 是第一道交付门槛：在固定模板安全导出尚未验证前，不应投入大量平台抓取实现。T04 与 T05 可以在 T03 完成后并行推进；T06 必须建立在截图命名和路由结果稳定之后。
