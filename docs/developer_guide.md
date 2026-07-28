# 开发者指南

## 开始前

开发以 [requirements.md](../requirements.md)、[design.md](../design.md)、[template_contract.md](template_contract.md)、[task_breakdown.md](task_breakdown.md) 和 [ai_coding_constraints.md](ai_coding_constraints.md) 为准。README 只提供导航，不是字段映射的权威来源。

运行环境为 Windows 10/11、Python 3.11+、Microsoft Excel 和 Playwright Chromium。安装命令：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## 开发原则

1. `template/` 是只读源；所有写入只能针对任务 staging 副本。
2. 模板工作簿禁止用 `openpyxl`、pandas 或 xlsxwriter 另存；只允许 Excel COM 直接保存副本。
3. 采集事实与模板行必须分离：`RecordResult` 保存完整事实，`TemplateRow` 只保存模板允许的字段。
4. 模板标准枚举和列映射集中于 `src/domain/template_schema.py`；平台域名、分类和选择器集中于 `src/crawler/platform_catalog.py`，不得散落在 UI 中。
5. 每个异步任务必须可以取消；浏览器、页面、Excel COM 和临时文件都必须在 `finally` 中释放。
6. 不实现代理池、反检测、验证码处理、未经用户授权的 CDP 连接或访问控制绕过。

## 模块职责

| 模块 | 职责 | 不应承担的职责 |
| --- | --- | --- |
| `input/` | 读取用户文件、抽取和稳定去重 URL | 路由平台、写 Excel。 |
| `crawler/` | 页面访问、限速、重试、字段提取和平台路由 | 直接生成模板行或控制 GUI。 |
| `screenshot/` | 管理浏览器 context、主截图、主页截图和临时 OCR 图片 | 决定 Excel 列。 |
| `domain/` | 稳定模型、模板结构和枚举 | I/O、UI 或浏览器调用。 |
| `export/` | 模板复制、行映射、Excel COM、资产校验、ZIP | 重新抓取网页。 |
| `services/` | 协调完整任务、发布进度事件、处理取消 | 具体页面 DOM 选择器。 |
| `tools/` | 识别受限页、内容失效和错误响应，提供有界人工处理等待 | 隐匿自动化、破解验证码或绕过权限。 |
| `ui/` | 收集参数、显示进度和运行态审计信息 | 在主线程执行阻塞任务。 |

## 模板开发流程

1. `TemplateManager` 对源 `template/` 计算 SHA-256 清单并复制到 staging。
2. `TemplateRowMapper` 只接收 `READY_FOR_EXPORT` 的记录，并校验主截图与标准枚举；其他缺失字段保持空白。
3. `ExcelTemplateWriter` 在短生命周期、隔离的 Excel COM worker 中打开副本，清空第 3 行及后的业务值，写入新数据并直接 `Save()`；worker 在退出前调用 `Quit()`，避免 COM 释放影响 UI 进程。
4. `PackageValidator` 从 Excel 读取截图/附件列，确认每一个引用都在 staging 根目录存在。
5. `Packager` 使用临时压缩包和原子替换生成 `template.zip`。

任何一步失败都不能修改源模板或生成看似成功的 ZIP。

## 平台扩展

新增平台前必须先确认其在固定模板的允许枚举中有明确落点。实现顺序：

1. 确认平台值已经存在于 `template_schema.py` 的固定模板枚举。
2. 在 `crawler/platform_catalog.py` 注册域名、路径优先级、提取器类别和平台选择器。
3. 写入 fixture 和路由、提取、模板映射测试。
4. 在可访问的真实页面上做人工验证；访问受限时扩展 `tools/page_access.py` 的强特征并记录为待人工补录，而不是以猜测数据通过导出。

不能通过新增“其他”平台值或篡改模板下拉选项绕过路由问题。

## 测试

```powershell
python -m pytest -m "not excel and not external"
python -m pytest tests/test_crawler/test_engine_playwright.py
python -m pytest -m ui tests/test_ui
python -m pytest -m excel tests/contract
python tools/release_check.py
```

测试分层如下：

- 单元测试：URL、枚举、路由、字段映射、资产命名和 ZIP 清单。
- 资产测试：使用内存响应和本地文件，覆盖 MIME/文件头不一致、超限、重复 URL、登录墙以及失败文件不进入附件集合。
- Playwright 集成测试：只访问本地 fixture，覆盖重定向、正文解析，以及每条最多一个页面截图和一个作者主页截图。
- 服务与 UI 测试：使用依赖替身覆盖成功、取消、全失败、合并重试和 QThread 信号；Qt 使用 offscreen 平台验证布局。
- 模板契约测试：仅在 Windows + Microsoft Excel 环境运行，验证源模板哈希、工作表顺序、首行、数据验证、保护和附件引用不变。
- 端到端契约测试：本地 HTTP fixture 经 Playwright、TaskRunner 和真实 Excel COM 生成最终 ZIP。

真实站点诊断测试标记为 `external` 并默认跳过；只有显式设置 `POR_RUN_EXTERNAL_TESTS=1` 时才运行。普通测试不得使用真实 Cookie 或写入源 `template/`。

`tools/release_check.py` 会解析全部 Python 文件、检查 500 行上限、验证 Markdown 本地链接、输出源模板指纹，并可通过 `--archive <path>` 检查 ZIP 路径和运行态文件泄漏。

## 完成定义

一次功能改动只有同时满足以下条件才可认为完成：需求文档已同步、单元测试覆盖主要分支、模板契约未被破坏、源模板清单未改变、最终 ZIP 没有缺失引用或额外运行态文件。
