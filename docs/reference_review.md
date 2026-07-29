# 参考项目评估与采用边界

## 结论

本项目采用“MediaCrawler 主项目的任务生命周期与适配器思想 + 浏览器插件的模板契约意识”，并使用比两者都更小的任务编排结构。固定 `template.xlsx` 是项目的硬边界，优先级高于任何参考实现。

## MediaCrawler-main

阅读重点包括 `base/base_crawler.py`、`tools/app_runner.py`、`tools/browser_launcher.py`、`tools/cdp_browser.py`、平台目录和架构文档。

采纳：异步入口的取消与清理、浏览器资源所有权、平台实现与公共接口分离、可选登录态以及面向失败恢复的配置。

不采纳：代理池、反检测脚本、验证码/滑块处理、请求签名、自动 CDP 启动或连接真实用户浏览器、搜索/创作者爬取模式、多数据库与多存储工厂。这些能力会扩大攻击与维护面，且不能帮助保留固定模板。

## MediaCrawler-new-main

采纳：较紧凑的基础结构和按平台逐步扩展的节奏。

不采纳：以模块级可变全局配置组织运行状态、平台模型贯穿整个存储层的方式。这里采用显式 `AppConfig`、`RecordResult` 和 `TemplateRow`，使运行态事实与交付模板分离。

## 浏览器插件

阅读重点包括 `side_panel.js`、`background.js`、`parse-excel.js` 和内置模板处理。

采纳：先读取工作表、表头、示例行和下拉规则，再填写数据；截图文件名与 Excel 单元格建立关联；交付时同时校验数据与文件。

不采纳：使用 SheetJS 重建新工作簿、只导出当前工作表、让浏览器逐个下载 Excel 和图片。当前模板受保护且包含 8 张工作表，必须复制后以 Excel COM 保存原生副本，再统一打包。

## 最终实现边界

- 默认标准 Playwright context；用户可明确导入合法 Cookie/storage state 或可视化登录。
- 不默认 CDP 连接用户正在使用的浏览器，避免干扰用户会话与浏览器资源所有权不清的问题。
- 不实现绕过验证码、付费墙、访问控制、风控或速率限制的机制。
- 不复制参考项目的源代码或许可证文本；只借鉴公开的架构思路。
- 模板写入采用 Office Open XML 直接操作（`OoxmlTemplateWriter`）为主路径，Excel COM 为旧式 OLE 回退。此决策解决了 MediaCrawler 的 COM 依赖问题，同时保留浏览器插件式的模板完整性校验。
