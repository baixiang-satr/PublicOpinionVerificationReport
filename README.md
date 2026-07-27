# 舆情验证报告工具

<div align="center">

**Public Opinion Verification Report Tool**

一个桌面应用程序，用于自动爬取链接页面内容、截取页面截图，并生成结构化的 Excel 验证报告。

</div>

---

## 📋 目录

- [项目简介](#-项目简介)
- [核心功能](#-核心功能)
- [完整项目结构](#-完整项目结构)
- [快速开始](#-快速开始)
- [模块设计说明](#-模块设计说明)
- [技术栈](#-技术栈)
- [数据流向](#-数据流向)
- [输出说明](#-输出说明)
- [参考项目](#-参考项目)
- [开发指南](#-开发指南)

---

## 📖 项目简介

本项目是一个**桌面端舆情验证报告工具**，核心工作流程如下：

```
输入文件（TXT/CSV/XLSX，内含链接）
        │
        ▼
  逐条爬取链接页面内容
        │
        ▼
  截取每个链接的页面截图
        │
        ▼
  识别发布者并截取其主页截图
        │
        ▼
  生成 template.xlsx（结构化数据报告）
        │
        ▼
  打包为 ZIP 压缩包输出
```

### 适用场景

- 舆情监控与取证
- 链接内容批量验证
- 社交媒体帖子存档
- 网页证据固定

---

## 🚀 核心功能

| 功能 | 描述 |
|------|------|
| **多格式输入** | 支持 TXT（每行一个链接）、CSV（自动识别列）、XLSX（选择工作表） |
| **自动编码检测** | 自动识别文件编码（UTF-8 / GBK / UTF-16 等） |
| **并发爬取** | 可配置的并发数，高效批量获取页面内容 |
| **智能解析** | 提取./template/template.xlsx中包含的结构化信息 |
| **页面截图** | 使用 Playwright 对每个链接页面进行完整截图 |
| **作者主页截图** | 自动识别发布者主页链接，并截图保存 |
| **Excel 报告生成** | 将所有数据汇总为 template.xlsx，含截图文件名对应关系 |
| **ZIP 打包输出** | 将 xlsx 和所有截图打包为标准压缩包 |
| **桌面 GUI** | 基于 PyQt5 的图形界面，操作直观 |
| **实时日志** | 运行过程中实时显示日志，方便排查问题 |

---

## 📁 完整项目结构

```
PublicOpinionVerificationReport/
│
├── .gitignore                          # Git 忽略规则
├── .editorconfig                       # 编辑器统一配置（缩进/编码/换行符）
├── .dockerignore                       # Docker 构建忽略配置
├── requirements.txt                    # Python 依赖清单（含来源说明）
├── pyproject.toml                      # 项目元数据（PEP 621 标准）
├── README.md                           # 本文件
│
├── src/                                # ========== 源代码目录 ==========
│   ├── main.py                         # 应用入口，解析命令行参数并启动
│   │
│   ├── config/                         # ----- 配置模块 -----
│   │   ├── __init__.py
│   │   ├── settings.py                 # 全局配置类（AppConfig）
│   │   │                                #   爬虫并发数、超时、延迟
│   │   │                                #   浏览器无头模式、窗口尺寸
│   │   │                                #   截图格式、输出路径等
│   │   └── logging_config.py           # 日志配置（控制台+文件双输出）
│   │
│   ├── input/                          # ----- 输入处理模块 -----
│   │   ├── __init__.py
│   │   ├── reader.py                   # 文件读取器
│   │   │                                #   - 自动检测编码（chardet）
│   │   │                                #   - 解析 TXT（逐行读取）
│   │   │                                #   - 解析 CSV（自动识别分隔符）
│   │   │                                #   - 解析 XLSX（openpyxl）
│   │   └── url_parser.py              # URL 解析与验证
│   │                                    #   - 正则提取 HTTP/HTTPS 链接
│   │                                    #   - 去重、过滤无效 URL
│   │                                    #   - URL 标准化
│   │
│   ├── crawler/                        # ----- 爬虫引擎模块 -----
│   │   ├── __init__.py
│   │   ├── engine.py                   # 爬虫主引擎
│   │   │                                #   - URL 任务队列管理
│   │   │                                #   - 并发调度（asyncio）
│   │   │                                #   - 超时/重试逻辑
│   │   │                                #   - 结果聚合
│   │   ├── fetcher.py                  # HTTP 页面获取器
│   │   │                                #   - httpx 异步请求
│   │   │                                #   - 自定义 Headers/Cookies
│   │   │                                #   - 自动跟随重定向
│   │   ├── content_parser.py          # 页面内容解析器
│   │   │                                #   提取: title, content,
│   │   │                                #          author, publish_time
│   │   └── author_extractor.py        # 作者信息提取器
│   │                                    #   - 从页面识别作者
│   │                                    #   - 提取作者主页 URL
│   │
│   ├── screenshot/                     # ----- 截图模块 -----
│   │   ├── __init__.py
│   │   ├── browser.py                  # 浏览器管理器
│   │   │                                #   - Playwright 生命周期
│   │   │                                #   - 无头/可视模式切换
│   │   │                                #   - 上下文管理
│   │   ├── page_shooter.py            # 页面截图器
│   │   │                                #   - 截图链接页面
│   │   │                                #   - 全页/视口截图
│   │   │                                #   - 等待元素加载
│   │   └── author_shooter.py          # 作者主页截图器
│   │                                    #   - 截图作者个人主页
│   │                                    #   - 命名规范: XXX主页.jpg
│   │
│   ├── export/                         # ----- 导出模块 -----
│   │   ├── __init__.py
│   │   ├── excel_writer.py            # Excel 写入器
│   │   │                                #   - 创建工作簿
│   │   │                                #   - 写入表头和数据
│   │   │                                #   - 设置单元格样式
│   │   │                                #   - 保存为 template.xlsx
│   │   ├── template_manager.py        # 模板目录管理器
│   │   │                                #   - 创建时间戳目录
│   │   │                                #   - 整理截图文件
│   │   │                                #   - 复制 xlsx 到目录
│   │   └── packager.py                # ZIP 打包器
│   │                                    #   - 模板目录 → ZIP
│   │                                    #   - 保持目录结构
│   │
│   ├── ui/                             # ----- 桌面 UI 模块 -----
│   │   ├── __init__.py
│   │   ├── app.py                      # 应用初始化
│   │   │                                #   - QApplication 设置
│   │   │                                #   - 全局样式加载
│   │   ├── main_window.py             # 主窗口
│   │   │                                #   布局:
│   │   │                                #     ├─ 文件选择区
│   │   │                                #     ├─ 参数配置区
│   │   │                                #     ├─ 进度显示区
│   │   │                                #     ├─ 日志查看区
│   │   │                                #     └─ 操作按钮区
│   │   └── widgets/                    # UI 组件集合
│   │       ├── __init__.py
│   │       ├── file_selector.py       # 文件选择组件
│   │       ├── progress_panel.py      # 进度面板
│   │       └── log_viewer.py          # 日志查看器
│   │
│   └── utils/                          # ----- 工具模块 -----
│       ├── __init__.py
│       ├── logger.py                   # 日志工具
│       ├── url_utils.py               # URL 工具函数
│       └── file_utils.py              # 文件系统工具
│
├── resources/                          # ========== 资源文件 ==========
│   ├── icons/                          # 程序图标
│   └── styles/                         # UI 样式表
│
├── output/                             # ========== 输出目录（自动生成）==========
│                                        #   每次运行在此生成时间戳子目录
│                                        #   例如: output/20260727_143000/
│                                        #           ├── template.xlsx
│                                        #           ├── 001.jpg
│                                        #           ├── 001主页.jpg
│                                        #           ├── 002.jpg
│                                        #           ├── 002主页.jpg
│                                        #           └── ...
│
├── tests/                              # ========== 单元测试 ==========
│   ├── __init__.py
│   ├── conftest.py                     # pytest 共享夹具
│   ├── test_input/                     # 输入处理测试
│   ├── test_crawler/                   # 爬虫引擎测试
│   ├── test_screenshot/               # 截图模块测试
│   └── test_export/                   # 导出模块测试
│
└── docs/                               # ========== 文档 ==========
    ├── images/                         # 文档用图
    ├── user_guide.md                   # 用户使用指南
    └── developer_guide.md              # 开发者指南
```

---

## 🚀 快速开始

### 环境要求

- Python >= 3.11
- Windows 10 / 11（推荐）

### 安装步骤

```bash
# 1. 克隆项目
git clone <仓库地址>
cd PublicOpinionVerificationReport

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
playwright install chromium
```

### 运行

```bash
# 方式一：GUI 桌面模式
python src/main.py

# 方式二：命令行模式（待实现）
python src/main.py --input links.txt --output ./output
```

---

## 🧩 模块设计说明

### 1. 配置模块 (`src/config/`)

参考 [MediaCrawler-main/config/base_config.py]，将所有配置集中管理。

**设计原则：**
- 使用 `dataclass` 或 `pydantic` 模型管理配置
- 支持从 `settings.py` 和环境变量读取
- 分类管理：爬虫配置、浏览器配置、截图配置、导出配置、日志配置

**关键配置项：**

| 分类 | 配置项 | 说明 |
|------|--------|------|
| 爬虫 | `MAX_CONCURRENCY` | 最大并发爬取数（默认 3） |
| 爬虫 | `PAGE_LOAD_TIMEOUT` | 页面加载超时（默认 30s） |
| 爬虫 | `CRAWL_DELAY` | 请求间隔（默认 1s） |
| 爬虫 | `MAX_RETRIES` | 最大重试次数（默认 3） |
| 浏览器 | `HEADLESS` | 是否无头模式（默认 True） |
| 浏览器 | `WINDOW_WIDTH/HEIGHT` | 窗口大小（1920x1080） |
| 截图 | `SCREENSHOT_FORMAT` | 截图格式（png/jpeg） |
| 截图 | `FULL_PAGE_SCREENSHOT` | 是否全页截图（默认 True） |
| 导出 | `OUTPUT_DIR` | 输出目录（默认 output/） |

---

### 2. 输入处理模块 (`src/input/`)

参考 [浏览器插件/parse-excel.js] 的文件解析逻辑。

**功能流程：**
```
用户选择文件
    │
    ├── TXT 文件 → 逐行读取 → chardet 检测编码
    │
    ├── CSV 文件 → csv.reader 解析 → 自动识别列头
    │
    └── XLSX 文件 → openpyxl.load_workbook → 选择工作表
                        │
                        ▼
                提取所有文本内容
                        │
                        ▼
                url_parser 正则提取 URL
                        │
                        ▼
                去重 → 验证 → 返回 URL 列表
```

**支持的文件格式：**
- `.txt` — 每行一个链接，支持 UTF-8/GBK 等编码
- `.csv` — 逗号/制表符分隔，自动检测包含链接的列
- `.xlsx` — 标准 Excel 文件，用户选择工作表

---

### 3. 爬虫引擎模块 (`src/crawler/`)

参考 [MediaCrawler-main/media_platform/] 的爬虫架构。

**架构设计：**
```
URL 列表
    │
    ▼
engine.py（调度器）
    │
    ├── 创建任务队列（asyncio.Queue）
    ├── 启动 N 个 worker 协程（N = MAX_CONCURRENCY）
    │
    ├── Worker 1 → fetcher.fetch(url) → content_parser.parse(html)
    ├── Worker 2 → fetcher.fetch(url) → content_parser.parse(html)
    ├── Worker 3 → fetcher.fetch(url) → content_parser.parse(html)
    └── ...
    │
    └── 结果聚合 → 返回 CrawlResult 列表
```

**CrawlResult 数据结构：**
```python
@dataclass
class CrawlResult:
    url: str                    # 原始 URL
    title: str                  # 页面标题
    content: str                # 正文内容（纯文本）
    author: str                 # 作者/发布者名称
    author_url: str             # 作者主页 URL
    publish_time: str           # 发布时间
    images: List[str]           # 页面中的图片 URL
    status_code: int            # HTTP 状态码
    error: Optional[str]        # 错误信息（爬取失败时）
```

---

### 4. 截图模块 (`src/screenshot/`)

参考 [浏览器插件] 的截图功能实现，使用 Playwright 操控浏览器。

**设计说明：**

```python
# 截图文件名规范
#   第 N 个链接的页面截图 → {序号:03d}.jpg   （如 001.jpg）
#   第 N 个链接的作者主页  → {序号:03d}主页.jpg（如 001主页.jpg）
```

**browser.py — 浏览器管理器：**
- 封装 Playwright 的 `async with async_playwright() as p` 生命周期
- 管理浏览器上下文（Cookies、本地存储、代理）
- 支持无头模式（后台运行）和可视化模式（调试用）

**page_shooter.py — 页面截图器：**
- 打开 URL，等待 `networkidle` 状态
- 设置视口大小为 1920x1080
- 支持全页截图（`full_page=True`）和视口截图
- 自动处理页面加载超时

**author_shooter.py — 作者主页截图器：**
- 接收 `author_url` 列表
- 对每个作者主页执行截图
- 使用与 page_shooter 相同的截图参数
- 按规范命名保存

---

### 5. 导出模块 (`src/export/`)

参考 [浏览器插件/exceljs.min.js + template.xlsx] 的输出格式。

**excel_writer.py — Excel 写入器：**

生成 template.xlsx 的表结构：

| 序号 | URL | 页面标题 | 作者 | 发布时间 | 页面截图 | 作者主页截图 | 状态 |
|------|-----|---------|------|---------|---------|------------|------|
| 1 | https://... | 示例标题 | 张三 | 2026-07-27 | 001.jpg | 001主页.jpg | 成功 |

**template_manager.py — 模板目录管理器：**

最终输出的目录结构：
```
template/
├── template.xlsx
├── 001.jpg
├── 001主页.jpg
├── 002.jpg
├── 002主页.jpg
└── ...
```

**packager.py — ZIP 打包器：**
- 使用 `shutil.make_archive` 压缩
- 输出为 `舆情验证报告_YYYYMMDD_HHMMSS.zip`

---

### 6. 桌面 UI 模块 (`src/ui/`)

基于 PyQt5 构建的桌面图形界面。

**主窗口布局：**
```
┌─────────────────────────────────────────────────┐
│  舆情验证报告工具 v0.1.0                        │
├─────────────────────────────────────────────────┤
│  📂 输入文件: [__________________] [选择文件]    │
│  格式: TXT / CSV / XLSX                         │
├─────────────────────────────────────────────────┤
│  并发数: [3]  🔴 无头模式 [✓]                  │
│  截图格式: [PNG ▼]  全页截图 [✓]              │
├─────────────────────────────────────────────────┤
│  ████████████████░░░░░░░  70%                   │
│  正在处理: 3/10  成功: 3  失败: 0              │
├─────────────────────────────────────────────────┤
│  [12:30:01] 开始爬取第 1 个链接...              │
│  [12:30:02] ✓ 成功获取: https://example.com     │
│  [12:30:05] 开始截图第 1 个页面...              │
│  [12:30:08] ✓ 截图完成: 001.jpg                │
├─────────────────────────────────────────────────┤
│        [▶ 开始]  [⏸ 暂停]  [📦 导出]          │
└─────────────────────────────────────────────────┘
```

**组件拆分：**
- `file_selector.py` — 文件选择 + 拖拽支持
- `progress_panel.py` — 进度条 + 计数统计
- `log_viewer.py` — 实时日志显示

---

## 🔄 数据流向

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐
│  输入文件    │────▶│  读取与解析   │────▶│  URL 列表      │
│ (TXT/CSV/   │     │  reader.py   │     │  url_parser.py │
│  XLSX)      │     │              │     │                │
└─────────────┘     └──────────────┘     └───────┬────────┘
                                                 │
                                                 ▼
┌──────────────────────────────────────────────────────────┐
│                    爬虫引擎 (engine.py)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                │
│  │ Worker 1 │  │ Worker 2 │  │ Worker 3 │  ...           │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                │
│       │              │              │                      │
│       ▼              ▼              ▼                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                │
│  │ fetcher  │  │ fetcher  │  │ fetcher  │                │
│  │ +parser  │  │ +parser  │  │ +parser  │                │
│  └──────────┘  └──────────┘  └──────────┘                │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│                   结果集 (CrawlResult 列表)                │
│  ┌──────────┬──────────┬──────────┬────────────────┐     │
│  │ url      │ title    │ author   │ author_url     │     │
│  │ content  │ pub_time │ images   │ status_code    │     │
│  └──────────┴──────────┴──────────┴────────────────┘     │
└──────────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│                   截图模块 (screenshot/)                   │
│  ┌──────────────────────┐  ┌────────────────────────┐    │
│  │ page_shooter.py      │  │ author_shooter.py      │    │
│  │ 每个链接页面 → 截图   │  │ 作者主页 → 截图        │    │
│  │ 001.jpg, 002.jpg...  │  │ 001主页.jpg, ...       │    │
│  └──────────────────────┘  └────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│                   导出模块 (export/)                       │
│  ┌──────────────────┐  ┌─────────────────────────────┐   │
│  │ excel_writer.py  │  │ template_manager.py         │   │
│  │ 生成 template.xlsx│  │ 整理截图 → 组织模板目录     │   │
│  └──────────────────┘  └──────────┬──────────────────┘   │
│                                   │                       │
│                            ┌──────▼──────┐               │
│                            │ packager.py │               │
│                            │ 打包 → ZIP  │               │
│                            └─────────────┘               │
└──────────────────────────────────────────────────────────┘
```

---

## 🛠 技术栈

| 技术 | 用途 | 参考来源 |
|------|------|---------|
| **Python 3.11+** | 编程语言 | — |
| **PyQt5** | 桌面 GUI 框架 | — |
| **Playwright** | 浏览器自动化与截图 | MediaCrawler |
| **httpx** | 异步 HTTP 客户端 | MediaCrawler |
| **requests** | 同步 HTTP 客户端（备选） | MediaCrawler |
| **beautifulsoup4 + lxml** | HTML 内容解析 | MediaCrawler |
| **parsel** | CSS/XPath 选择器 | MediaCrawler |
| **openpyxl** | Excel 读写 | 浏览器插件 |
| **chardet** | 文件编码检测 | — |
| **python-dotenv** | 环境变量管理 | MediaCrawler |
| **pydantic** | 数据模型验证 | MediaCrawler |
| **asyncio** | 异步并发支持 | MediaCrawler |
| **pytest** | 单元测试 | MediaCrawler |

---

## 📤 输出说明

### 输出目录结构

每次运行完成后，在 `output/` 目录下生成一个以时间戳命名的文件夹：

```
output/
└── 验证报告_20260727_143000/
    ├── template.xlsx         ← 结构化数据报告
    ├── 001.jpg               ← 第1个链接的页面截图
    ├── 001主页.jpg            ← 第1个链接的作者主页截图
    ├── 002.jpg               ← 第2个链接的页面截图
    ├── 002主页.jpg            ← 第2个链接的作者主页截图
    ├── 003.jpg
    ├── 003主页.jpg
    └── ...
```

### template.xlsx 说明

| 列名 | 内容 | 说明 |
|------|------|------|
| 序号 | 1, 2, 3... | 自增序号 |
| URL | https://... | 原始输入链接 |
| 页面标题 | 页面 `<title>` 内容 | 爬取所得 |
| 作者 | 发布者名称 | 从页面提取 |
| 作者主页 | https://... | 作者个人主页 URL |
| 发布时间 | 2026-07-27 14:30 | 从页面提取 |
| 正文摘要 | 前 200 字 | 页面正文内容 |
| 页面截图 | 001.jpg | 对应截图文件名 |
| 作者主页截图 | 001主页.jpg | 对应截图文件名 |
| 状态 | 成功 / 失败 | 爬取结果 |
| 备注 | — | 错误信息等 |

---

## 📚 参考项目

本项目参考了 `references/` 目录下的三个开源项目：

### 1. MediaCrawler-main

- **用途**: 自媒体平台爬虫框架
- **借鉴内容**:
  - 项目整体架构（config/ / test/ / docs/ 分层）
  - 爬虫引擎设计模式（异步并发 + Worker 调度）
  - Playwright 浏览器自动化集成方式
  - 日志配置、异常处理、重试机制
  - 依赖管理和项目元数据配置（pyproject.toml）

### 2. MediaCrawler-new-main

- **用途**: 简化版爬虫框架
- **借鉴内容**:
  - 精简的项目结构
  - Base 基类设计模式
  - 平台通用抽象层

### 3. 浏览器插件（Chrome Extension）

- **用途**: 网页数据采集与 Excel 导出
- **借鉴内容**:
  - Excel 文件解析与生成（parse-excel.js + exceljs）
  - 网页截图功能流程
  - template.xlsx 的输出格式规范
  - 用户操作流程设计

---

## 💻 开发指南

参见 [docs/developer_guide.md](docs/developer_guide.md)。

### 核心开发原则

1. **代码不加注释** — 所有说明性内容写入本 README 和 docs/ 下的 .md 文件
2. **模块职责单一** — 每个 .py 文件只负责一个明确的职责
3. **配置集中管理** — 所有可调参数放在 `config/settings.py`
4. **错误可追溯** — 统一日志格式，关键步骤记录日志

### 关键依赖版本

| 包名 | 最低版本 | 说明 |
|------|---------|------|
| playwright | 1.61.0 | 浏览器自动化，需额外 `playwright install chromium` |
| httpx | 0.28.0 | 异步 HTTP，支持 HTTP/2 |
| openpyxl | 3.1.2 | Excel 读写，不支持 .xls 格式 |
| PyQt5 | 5.15.0 | GUI 框架 |
| beautifulsoup4 | 4.13.0 | HTML 解析 |

---

## 📄 许可证

本项目仅供学习和参考之用。

---

## ⚠️ 免责声明

本工具仅用于合法合规的信息采集与验证。使用者应遵守相关法律法规，不得将本工具用于非法用途。
