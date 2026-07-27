# 舆情验证报告工具

本项目是一个 Windows 本地桌面工具：导入网页 URL，采集页面基础信息、截图和图片附件，并生成固定格式的 `template.zip`。

当前仓库处于“需求与架构已确定、业务代码骨架待实现”阶段。`src/` 中的大多数模块仍为占位骨架，不能把当前版本视为可运行交付物。

## 交付契约

- [template/](template/) 是只读基准，程序绝不能修改其中任何文件。
- 每个任务复制基准目录到独立 staging 区，在副本中填充结果。
- 最终文件名固定为 `template.zip`，其内部保留 `template/` 顶层目录。
- `template.xlsx` 的工作表、表头、下拉值、格式和保护必须保持不变；截图和附件放在同一目录。
- Excel 中每一个非空截图/附件文件名必须对应 ZIP 内的实际文件。

固定模板为受保护的 Office OLE 工作簿，不能用 `openpyxl` 重建或另存。`openpyxl` 仅用于读取用户输入的普通 `.xlsx`；模板副本必须通过 Windows Excel COM 自动化写入并验证。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [requirements.md](requirements.md) | 业务范围、8 张工作表、字段映射、标准枚举和验收标准。 |
| [design.md](design.md) | 模块设计、数据模型、任务状态机、模板写入、GUI 和测试方案。 |
| [docs/template_contract.md](docs/template_contract.md) | 固定模板与 `template.zip` 的开发检查清单。 |
| [docs/reference_review.md](docs/reference_review.md) | 三个参考项目的取舍与本项目最终选择。 |
| [docs/developer_guide.md](docs/developer_guide.md) | 开发环境、模块职责、测试和完成定义。 |
| [docs/user_guide.md](docs/user_guide.md) | 完成实现后的安装、操作流程和限制说明。 |

## 目标架构

```text
输入文件 -> URL 解析/去重 -> 任务编排 -> Playwright 页面访问
  -> 平台路由与字段提取 -> 截图/图片附件 -> 模板行校验
  -> Excel COM 写入模板副本 -> 附件引用校验 -> template.zip
```

运行态会保留标题、作者主页 URL、状态码、重定向链和错误信息；其中模板没有对应列的字段只在 GUI 和本机运行日志中显示，不会擅自加入 ZIP。

## 项目目录

```text
src/
├── config/                 # 不可变模板配置与可覆盖任务配置
├── domain/                 # 任务、抓取结果、模板结构模型
├── input/                  # TXT / CSV / 普通 XLSX 的 URL 导入
├── crawler/                # 调度器、路由器、通用与平台提取器
├── screenshot/             # 浏览器池、页面/主页截图、图片附件
├── export/                 # 模板副本、行映射、Excel COM、校验和打包
├── services/               # 端到端任务编排
├── ui/                     # PyQt5 界面与后台 worker
└── utils/                  # 文件、时间、日志和 URL 工具

tests/
├── test_input/
├── test_crawler/
├── test_screenshot/
├── test_export/
└── contract/               # 仅 Windows + Excel 环境运行的模板契约测试
```

## 开发环境

前置条件：Windows 10/11、Python 3.11+、可自动化的 Microsoft Excel，以及 Playwright Chromium。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
pytest
```

真实的模板导出契约测试必须在已安装 Microsoft Excel 的 Windows 环境中执行；普通单元测试不应启动 Excel 或访问外部站点。

## 参考项目取舍

`references/MediaCrawler-main` 的任务生命周期、资源清理、浏览器上下文和平台适配器设计被吸收；`MediaCrawler-new-main` 提供了保持骨架精简的思路；浏览器插件帮助确认模板字段、示例行和截图附件关联。

本项目不采用参考项目中的代理池、反检测、验证码处理、默认 CDP 连接用户浏览器、重建单工作表或多数据库输出方案。默认使用隔离的 Playwright context，用户只能显式提供合法登录态或选择可视化登录。

## 合规边界

工具只处理用户有权处理的公开信息，不绕过验证码、付费墙、登录权限或站点访问控制。Cookie、令牌、浏览器 profile、调试 HTML 和本机日志均不得打入 `template.zip`。
