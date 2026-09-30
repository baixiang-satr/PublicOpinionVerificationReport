# 后期改进任务清单（TASKS）

本文档是平台后期定期更新改进的统一跟踪入口，格式与维护方式参考 `docs/task_breakdown.md`。每次发现可改进点时在此追加条目，后续有时间统一排期完善。

## 维护约定

- **编号**：新条目按 `U<序号>` 递增（当前已用至 U09），不复用已删除条目的编号。
- **状态标记**：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成。完成时必须在「当前状态」日期区记录完成日期与验证方式。
- **条目结构**：每条含**背景与现状**（附代码依据）、**决策**（已拍板的方案）、**涉及模块**、**验收标准**，可直接作为实现任务卡。
- **排期**：条目按优先级从高到低排列；实现时整条完成后独立验证，不把未完成的下游功能当作验收前提。

## 当前状态（2026-08-12）

- [x] U01：函文档统一入口（文件选择器 + 附件列末尾追加 + 自动入包）——2026-08-11 完成；`tests/test_export/test_letter_asset.py` + `test_task_runner` 函入包/降级用例通过
- [x] U02：退出确认弹窗（直接退出 / 最小化到任务栏）——2026-08-11 完成；`tests/test_webui/test_exit_control.py` 通过（三态交互待人工实测）
- [x] U03：抓取前登录态复验跳过（30 分钟新鲜期）——2026-08-11 完成；`tests/test_crawler/test_auth_preflight.py` 8 例通过（新鲜期跳过/超期复验/EXPIRED 不跳过/阈值可配）
- [x] U04：URL 有效性复验与批量删除——2026-08-11 完成；`tests/test_services/test_url_recheck.py` + `tests/test_webui/test_recheck_api.py` 通过（独立复验对话框形态；含死链任务端到端待人工实测）
- [x] U05：导出表格放开增删数据行（列结构与格式仍锁定）——2026-08-11 完成；`tests/test_export/test_ooxml_protection.py` 断言 insertRows/deleteRows=="0"，契约文档已同步（Excel/WPS 实测待人工）
- [x] U06：自动截图附带最终 URL（内容页 + 个人主页）——2026-08-11 完成初版注入横幅，因个人页 URL 失真同日改为全屏截图；2026-08-12 用户要求后台无感，最终改为 PrintWindow 直抓浏览器窗口（含地址栏 URL，窗口离屏亦可截）；`tests/test_screenshot/test_window_capture.py`、`test_url_ocr_filter.py` 通过
- [x] U07：重复 URL 选文件即提示（删除重复 / 全部保留）——2026-08-11 完成；`tests/test_input/` 去重保留首条/全部保留/重复检测用例通过
- [x] U08：复验失效判定修正 + 失效行高亮 + 微博删除页连锁修复（查看者主页截图）——2026-08-12 完成；`tests/test_tools/test_page_access_guards.py` 等 10 个新用例 + 全量 723 通过（10 条已删微博的端到端复验/高亮/一键删除与活页回归待人工实测）
- [x] U09：登录窗口固定内置 Chromium + 防闪烁稳定化——2026-09-30 完成；`tests/test_auth/` 45 例通过（含新 `test_login_window.py` 5 例）；微信公众号 90s 静置刷新计数冒烟（`tools/smoke_login_window_stability.py`）与双机实测待人工
- [x] U10：文字类平台内容页整页长图截图（自动+补录）——2026-09-30 完成；`tests/test_screenshot/test_long_capture.py`/`test_long_capture_ui.py`/`test_capture_config.py` + `tests/test_crawler/test_ocr_pipeline.py` 共 47 新用例，全量 877 通过（7 平台真机长图冒烟待人工）

---

## U01 函文档统一入口

**优先级**：高（每次出包都要手动操作，易遗漏）

**背景与现状**：

- 输出的 `template.zip` 中还需附一份「函」文档（如 `XX市申请处置的函.docx`），目前靠手动加入压缩包；且函名必须与模板表格的附件列（"其他文件名(多个逗号分隔)" / "其他附件文件名(多个逗号分隔)"）形成关联依赖。
- 附件列由 `TemplateRowMapper._attachment_names`（`src/export/row_mapper.py`）按截图/附件资产拼装。
- **硬约束**：`validate_template_assets`（`src/export/package_validator.py`）要求附件列引用的文件名必须在 zip 内真实存在，只写名字不放文件会导致导出校验失败 → 必须连文件一起入包。

**决策**（2026-08-10 与用户确认）：

- 采用**文件选择器**入口：用户选择函文件一次，平台自动完成①复制进 zip ②函名写入所有行附件列。
- 函名追加在附件列**末尾**，英文逗号连接，例如 `001主页.jpg,XX市申请处置的函.docx`（此为准，覆盖最初"写在最前"的说法）。
- 函文件扩展名不限（doc/docx/pdf/jpg 等），按实际文件名原样写入。
- 在抓取开始之前设置，对本批全部行（含手工补录行）生效。

**方案要点**：

1. `src/webui/bridge.py` 新增 `pick_letter_file()`：复用 `_pick_file` 弹原生选择框，文件名经 `require_safe_file_name` 校验后存入任务配置/会话状态，返回文件名供前端展示。
2. 前端 InputView 或 CrawlView 加入口按钮 + 已选文件名展示 + 清除操作。
3. `TemplateRowMapper._attachment_names` 末尾追加函名（去重、避开主截图名）。
4. 导出 staging 阶段把函文件复制进 template 目录（参考 `src/export/staging_assets.py`），保证打包校验通过。

**涉及模块**：`src/webui/bridge.py`、`src/export/row_mapper.py`、`src/export/staging_assets.py`、`src/export/packager.py`、`web/src/views/InputView.vue`（或 CrawlView.vue）。

**验收标准**：

- 选择函文件后开始抓取并导出，zip 内含函文件，所有行附件列末尾含函名且格式为 `已有附件,函名`（无已有附件时只有函名）。
- 不选函文件时导出行为与现状完全一致。
- 文件名含中文/空格/特殊字符时经安全名校验不报错；重复选择同名文件不产生重复列值。

---

## U02 退出确认弹窗

**优先级**：中

**背景与现状**：

- B/S 改造（2026-09-30）：pywebview 桌面壳已完全移除，`python -m src.main` 启动 FastAPI + Uvicorn（默认 127.0.0.1:16667，`POIR_HOST`/`POIR_PORT` 可覆盖）并自动打开浏览器；桥方法经 `POST /api/{method}` 暴露，文件选择改浏览器上传/下载，事件走 `WS /ws/events`。
- 误点关闭会直接中断正在进行的抓取/补录工作。

**决策**（2026-08-10 与用户确认）：

- 点击关闭时弹窗询问「直接退出 / 最小化到任务栏」，按用户指令行事。
- 关闭行为：浏览器标签页关闭不影响后台任务；停止服务用 Ctrl+C（shutdown 时执行 `bridge.capture.shutdown()`，关闭常驻截图浏览器、写回登录态）。

**方案要点**：

1. 订阅 `window.events.closing`：处理器返回 `False` 否决关闭，并通知前端弹出确认框。
2. 用户选「退出」→ 调 `window.destroy()` 走正常 shutdown 流程；选「最小化」→ 调 `window.minimize()`；选「取消」→ 不动。
3. 最小化期间后台抓取线程继续运行，事件推送（EventSink）保持；从任务栏恢复后界面状态不变。

**涉及模块**：`src/webui/app.py`、`src/webui/bridge.py`（或新增退出协调方法）、`web/src/App.vue`。

**验收标准**：

- 点关闭按钮弹出三态确认（退出/最小化/取消），三个分支行为正确。
- 抓取运行中最小化，任务不中断，恢复窗口后进度事件正常刷新。
- 选择退出后登录态写回、截图浏览器关闭等清理逻辑与现状一致。

---

## U03 抓取前登录态复验跳过

**优先级**：高（每次开抓白等一轮复验，体验差）

**背景与现状**：

- 点「开始抓取」后，`preflight_auth_profiles`（`src/crawler/auth_preflight.py`）会对本批涉及的全部平台跑 `revalidate_platform_profile`（`src/screenshot/auth_revalidation.py`）逐站探测，即使用户刚刚在「登录态管理」里登录验证过一遍。
- 登录/验证成功后 `AuthProfileStore.commit_validated_state` 已把 `validated_at`（ISO 时间戳）写入档案（`src/auth/store.py`、`src/auth/models.py` 的 `AuthProfile`）。

**决策**（2026-08-10）：

- 复验前检查平台档案：状态为 VALID 且 `validated_at` 距当前 **30 分钟内**则跳过该平台复验，直接视为有效；超过 30 分钟或状态非 VALID 维持现有复验流程。
- 阈值做成可配置项：`TaskConfig` 新增 `auth_preflight_freshness_minutes = 30`。

**方案要点**：

1. `preflight_auth_profiles` 的 probe 分支前置新鲜期判断（读 `AuthProfileStore.profile_for(key)`）。
2. 跳过时向前端发事件说明「30 分钟内已验证，跳过复验」。
3. 单元测试覆盖：新鲜期内跳过、超期复验、状态 EXPIRED 不复验走自愈。

**涉及模块**：`src/crawler/auth_preflight.py`、`src/config/settings.py`、`src/auth/store.py`、`tests/test_crawler/`。

**验收标准**：

- 登录态管理里完成登录后 30 分钟内开抓，不再出现「正在抓取前复验」等待；事件日志可见跳过说明。
- 超过 30 分钟开抓，复验行为与现状一致。
- 复验跳过不影响 EXPIRED 档案的自愈逻辑。

---

## U04 URL 有效性复验与批量删除

**优先级**：高（上报前防死链，直接影响交付质量）

**背景与现状**：

- 业务场景：URL 表格抓取一遍后，其他材料要隔一段时间才齐，此时间差内部分 URL 内容可能被平台删除，这些失效 URL 无需上报，应从结果中剔除。
- 失效判定能力已具备：`inspect_page_access` / `inspect_http_response`（`src/tools/page_access.py`）可识别 HTTP 404、CONTENT_UNAVAILABLE（"内容不存在/已删除/已下线/页面已失效"等文案）、重定向首页等。
- 单条删除已具备：`bridge.remove_record`（`src/webui/bridge.py`）+ `record_disposal.delete_record_artifacts`（`src/services/record_disposal.py`）。
- 历史任务重开已具备：`resume_checkpoint`（断点续跑）、`pick_zip_file` / `TemplateZipImporter`（zip 导入）。

**决策**（2026-08-10 与用户确认）：

- 新增「URL 有效性复验」功能：逐条访问待验 URL（只探测有效性，不重新截图），无效行在界面上标出（标红 / "已失效"徽标）。
- 删除方式三选：一键删除全部无效行、勾选多条一起删、逐条删。
- 入口双覆盖：当前抓取会话的结果/补录页面可用；重开历史任务（断点/zip 导入）后也可用。
- 删除后可重新导出 zip。

**方案要点**：

1. 后端新增复验任务（复用浏览器池与登录态，限速/取消语义与抓取一致），每条记录产出 有效/已失效/存疑（如风控、超时）三态结果并持久化。
2. `remove_record` 扩展批量删除接口（传 evidence_id 列表）。
3. 前端结果列表增加「复验 URL」按钮、失效标记列、复选框与批量操作条；「存疑」条目不纳入一键删除，仅可人工勾选。
4. 复验后质量报告/审计记录同步更新。

**涉及模块**：`src/tools/page_access.py`（复用）、`src/webui/bridge.py`、`src/webui/runner.py`（JobRunner）、`src/services/review_session.py`、`web/src/views/ResultView.vue` / `ReviewView.vue`。

**验收标准**：

- 对含已知死链的历史任务复验，死链行被正确标记；一键删除后重新导出的 zip 不含这些行。
- 勾选多条删除、逐条删除均可用；误删前（一键删除）有确认提示。
- 复验过程可取消；「存疑」条目不会被一键删除。
- 复验不改动截图与已填字段，只增删行级状态。

---

## U05 导出表格放开增删数据行

**优先级**：高（业务方拿到表格后需增删行，当前被保护锁定）

**背景与现状**：

- 2026-08-06 已实现数据区单元格解锁：`unlocked_style_map`（`src/export/sheet_protection.py`）为 cellXfs 生成「解锁克隆」，数据行内容可改；但 sheetProtection 仍锁定 insertRows/deleteRows，收件人无法在 Excel/WPS 里增删行。
- `normalize_sheet_protection` 的 `_BLOCKED_ACTION_ATTRIBUTES` 含 insertRows/deleteRows，防御性兜底会把显式放开改回锁定。
- 契约测试 `tests/test_export/test_ooxml_protection.py` 的 `test_sheet_protection_keeps_format_and_structure_blocked` 断言 insertRows/deleteRows != "0"；契约文档 `docs/template_contract.md` 第 11、18 行写明「增删行列一律锁定」，均需同步。
- **硬约束**：模板 8 表列结构为固定合同，insertColumns/deleteColumns/format* 必须保持锁定。

**决策**（2026-08-11 与用户确认）：

- 放开 insertRows/deleteRows（sheetProtection 显式写 "0" 允许），收件人可插入/删除数据行。
- 列结构不可增删、格式不可改，表头/示例行保持锁定；单元格内容可改维持现状。

**方案要点**：

1. `_BLOCKED_ACTION_ATTRIBUTES` 移除 insertRows/deleteRows；`normalize_sheet_protection` 显式写 insertRows="0"/deleteRows="0"（OOXML 语义：属性缺省=禁止，写 "0" 才放开）。
2. 更新 `test_ooxml_protection.py`：断言 insertRows/deleteRows == "0"，insertColumns/deleteColumns/formatCells 等仍 != "0"。
3. 更新 `docs/template_contract.md` 两处保护描述为「可增删数据行、列结构与格式锁定」。

**涉及模块**：`src/export/sheet_protection.py`、`tests/test_export/test_ooxml_protection.py`、`docs/template_contract.md`。

**验收标准**：

- Excel/WPS 打开导出 xlsx：可插入/删除数据行、可编辑单元格内容（新插入行继承上方数据行解锁样式可编辑）；不可增删列、不可改格式、表头/示例行仍锁定。
- openpyxl 校验 `ws.protection.insertRows is False` 且 `ws.protection.insertColumns is True`。
- 导出校验（validate_template_assets）与既有契约测试全部通过。

---

## U06 自动截图注入 URL 横幅

> **2026-08-12 最终方案**：注入横幅方案已废弃（个人页 SPA 上
> `location.href` 与地址栏最终 URL 不一致，横幅内容错误）；全屏
> ImageGrab 方案亦废弃（依赖窗口前台可见，违反后台无感要求）。最终
> 方案：`src/screenshot/window_capture.py`——标题 token 定位 HWND 后
> PrintWindow(PW_RENDERFULLCONTENT) 直抓浏览器窗口本体（标签栏+地址栏
> +页面同框），抓取浏览器离屏（background_crawl_browser）也能截，用户
> 无感；标签未激活时先激活再抓并恢复前台窗口；截图前先恢复真实标题
> （证据图不留 token）。OCR 过滤（`strip_banner_lines`）保留，剔除地址
> 栏 URL 行。以下为原始方案记录，仅供参考。

**优先级**：高（证据图必须可溯源 URL，交付合规要求）

**背景与现状**：

- 自动截图统一收口在 `PageShooter.capture_named`（`src/screenshot/page_shooter.py`，现 229 行余量充足）：内容页由 engine 经 `capture` 调用，个人主页由 `author_shooter.py` 三处调用 `capture_named`，均经同一入口，改一处即全覆盖。
- `page.screenshot` 只截页面内容，不含浏览器地址栏；人工框选（`src/screenshot/region_capture.py`，PIL.ImageGrab 冻结整屏）已含地址栏 URL，无需改动。
- 截图分支多：full_page / clip_region 文档坐标裁剪 / 视口兜底 / 抖音视频视口，横幅需在全部分支可见。
- 截图会被 OCR/字段恢复（`src/crawler/screenshot_field_recovery.py`）读取，横幅 URL 文本不得混入正文。

**决策**（2026-08-11 与用户确认）：

- 截图前向页面注入顶部横幅：显示重定向后的最终 URL（page.url），深色底白字、完整 URL 文本、最高 z-index 避免被页面遮挡；截图完成后移除，DOM 不留残留。
- 内容页与个人主页两类自动截图均生效。

**方案要点**：

1. 横幅注入 JS 放独立模块 `src/screenshot/url_banner_scripts.py`（参照 `region_capture_scripts.py` 拆分先例，避免 page_shooter 行数膨胀）。
2. 在 `capture_named` 的 `wait_for_capture_ready` / `hide_obstructive_login_overlays` 之后注入，finally 中移除。
3. 分支覆盖：full_page（Playwright 临时扩视口，fixed 横幅停在顶部）与 clip（fixed 元素绘制在当前滚动偏移处，横幅落在裁剪区顶部）均须验证。
4. OCR 过滤：`screenshot_field_recovery` / OCR 增强链路过滤横幅 URL 行（URL 形态易识别），不写入正文。

**涉及模块**：`src/screenshot/page_shooter.py`、新增 `src/screenshot/url_banner_scripts.py`、`src/screenshot/author_shooter.py`（验证三处调用覆盖）、`src/crawler/screenshot_field_recovery.py`、`tests/test_screenshot/`。

**验收标准**：

- 全页 / 裁剪 / 视口 / 抖音视频 / 作者主页各分支截图顶部均含完整最终 URL。
- 截图后页面 DOM 无横幅残留，后续作者截图等操作不受影响；空白图拒绝逻辑不受影响。
- OCR 增强文本不含横幅 URL；人工框选行为与现状一致。

---

## U07 重复 URL 选文件即提示

**优先级**：中（防重复抓取浪费配额与制造重复行，体验改进）

**背景与现状**：

- `build_url_tasks`（`src/input/url_parser.py`）按源顺序**保留**重复（契约测试 `tests/test_input/test_url_parser.py` 的 `test_build_url_tasks_keeps_source_order_duplicates_and_removes_fragments` 为证）。
- `web/src/views/InputView.vue` 第 46 行文案「重复链接会自动去重」与实现不符，属误导。
- `bridge.pick_input_file`（`src/webui/bridge.py`）只返回 url_count/rejected_count；`start_crawl` 按 input_path 重新 `read_url_input`，去重选择必须显式贯通到读取层。
- **坑**：bridge.py 长期贴 500 行上限，新逻辑优先放 input 模块。

**决策**（2026-08-11 与用户确认）：

- 选文件后立即检测重复（按 normalized_url 完全一致判定），有重复即弹窗「删除重复（保留首次出现）/ 全部保留」，展示重复条数与示例 URL。
- 用户选择随任务带入 start_crawl，不再二次询问；「全部保留」= 现状行为。

**方案要点**：

1. `url_parser.py` / `reader.py` 增加重复检测与去重参数：返回重复条数 + 示例 URL；dedupe=True 时每个 normalized_url 只保留首次出现。
2. `pick_input_file` 返回 duplicate_count 与示例；前端 ElMessageBox 确认后把选择存入 Pinia store；`start_crawl` 增加 dedupe 形参贯通 `read_url_input`。
3. 修正 InputView 误导文案为实际行为；`resume_checkpoint` 带 input_path 的分支同样应用去重选择。

**涉及模块**：`src/input/url_parser.py`、`src/input/reader.py`、`src/webui/bridge.py`、`web/src/views/InputView.vue`、`web/src/stores/job.ts`、`tests/test_input/`。

**验收标准**：

- 含重复 URL 的文件选择后弹窗，两个分支行为正确：删除后任务列表无重复 normalized_url 且 url_count 相应减少；保留则与现状完全一致。
- 无重复文件不弹窗；rejected（无效值）统计不受影响。
- 界面文案与实际行为一致；`tests/test_input/` 覆盖「去重保留首条」与「全部保留」两条路径。

---

## U08 复验失效判定修正与微博删除页连锁修复

**优先级**：高（复验误判有效 + 查看者主页截图污染交付，均为实测确认的功能性 BUG）

**背景与现状**（2026-08-12 用户实测任务 `output/20260812-095449-e62d7372`，10 条已删除微博 URL）：

- **复验全部误判「有效」**（`url_recheck.json` 10/10 valid）：双层根因——①旧纯错误页守卫要求标题**精确等于** 6 个固定值或正文 <800 可见字符，真实删除页带完整站点框架（导航/页脚/推荐流）必然超限；②真机探测（2026-08-12）证实微博删除页文案是「**暂无查看权限**」（标题仍为「微博正文 - 微博」），不在删除文案库中。另一漏检通道：SPA 删除错误框水合晚于 `domcontentloaded + 1500ms` 稳定等待。
- **失效行高亮不可见**：`UrlRecheckDialog.vue` 给 tr 设 `background`，Element Plus 2.x 单元格背景由 `--el-table-tr-bg-color` 变量绘制，完全盖住。
- **个人页截图截成查看者本人主页**（`author_decisions/001主页.decision.json` 铁证）：微博不在未命中剥离守卫名单 → 删除页内嵌载荷中的查看者 uid 被当作 author_id → 候选主页 `weibo.com/u/{查看者uid}` → `identity_verdict` 的「expected_id 出现在候选 URL 即 verified」被循环论证击穿。
- 第 4 步「打开补录表格」与「复验 URL 有效性」按钮被 `.head-row` 的 `space-between` 分散对齐拉开。

**决策**（2026-08-12 与用户确认）：

- 「已失效」口径不变：确证删除（404/重定向首页/删除文案经新守卫确认）才 invalid；登录墙/验证码/风控/超时仍「存疑」，不进一键删除。
- 不改 `identity_verdict` 的 URL 回声规则（抖音/头条既有验收依赖）；从输入端（载荷剥离 + 查看者链接过滤）保证 expected_id 可信。
- 高亮只做在复验对话框；补录表格保持与 template 一致。

**方案要点**：

1. 新建 `src/tools/page_access_guards.py`（page_access.py 已 491/500 行必须拆）：`content_unavailable_confirmed` 三规则——标题子串命中删除文案 / 正文前 400 可见字符内命中 / 正文 <800 字纯错误页；`_UNAVAILABLE_TEXT_MARKERS` 与纯错误页守卫一并迁入，`page_access.py` 回导；文案库增补微博实测真实文案「暂无查看权限 / 暂无权限查看」。
2. `recheck_runner._probe` 初检无屏障时有界重检（`_UNAVAILABLE_RECHECK_ATTEMPTS=4` × `_UNAVAILABLE_RECHECK_DELAY_MS=1000`，命中即停，测试可 monkeypatch 为 0）。
3. `content_parser.py` 微博纳入未命中剥离守卫（与 kuaishou 同规，`weibo_bid` 现成）；`generic.py` 作者链接候选过滤查看者本人主页（`$CONFIG.uid` → `/u/{uid}`）。
4. `UrlRecheckDialog.vue` 高亮改 `:deep(.el-table .invalid-row){ --el-table-tr-bg-color: #fef0f0 }`；`ReviewView.vue` 两按钮包 `.actions` 容器聚拢。

**涉及模块**：`src/tools/page_access.py`、`src/tools/page_access_guards.py`（新建）、`src/webui/recheck_runner.py`、`src/crawler/content_parser.py`、`src/crawler/extractors/generic.py`、`web/src/components/UrlRecheckDialog.vue`、`web/src/views/ReviewView.vue`、`tests/test_tools/test_page_access_guards.py`（新建）、`tests/test_crawler/test_page_access_tools.py`、`tests/test_crawler/test_content_parser_guards.py`、`tests/test_webui/test_recheck_api.py`。

**验收标准**：

- 新增 12 个用例（守卫三规则/长文不误判/含「暂无查看权限」真机快照形态/重检命中与有界/微博剥离）+ 全量 725 通过；release-check-ok；前端 build + vue-tsc 通过。
- 真机验证（2026-08-12 已完成）：`output/test-tmp/probe_recheck_diag.py` 以生产 `_probe` 路径实测 2 条被删微博均判 `invalid CONTENT_UNAVAILABLE`。
- 实测（待人工确认界面侧）：重开 `output/20260812-095449-e62d7372` **重新点「开始复验」**（对话框默认展示持久化的旧结果，必须重跑才覆盖）→ 10 条应全部「已失效」+ 整行淡红 → 一键删除 → 重新导出 zip 不含这些行。
- 实测回归：活着的微博复验判「有效」，文首规则不误伤真实页面（**待人工实测**）。

## U09 登录窗口固定内置 Chromium 与防闪烁稳定化

**优先级**：高（微信公众号登录窗口一直闪，完全无法扫码登录；不同电脑闪的程度不同，2026-09-30 用户实测确认的功能性 BUG）

**背景与现状**：

- 症状：登录态管理 → 单平台「登录 / 更新」，窗口白屏抖动重绘 + 页面不停自动刷新，微信公众号最严重；两台电脑程度不同。
- 代码核查：app 内无任何主动 reload 页面的逻辑（全库无 `reload` 调用），刷新循环来自页面自身 JS 对运行环境的反应。三个叠加根因：
  1. 交互登录窗口注入了 `stealth.min.js` 反检测脚本（人工登录官方域名无收益，且最可疑为刷新循环诱因）；
  2. 有头模式为抖音视频解码移除了 `--disable-gpu`（`browser_options.py`），GPU/驱动差异导致白屏抖动——登录窗口并不需要视频解码；
  3. 窗口尺寸与 125%/150% DPI 缩放机器不对齐（context 固定 `device_scale_factor=1` 但窗口未固定）。

**决策**（2026-09-30 与用户确认）：

- 登录窗口固定使用随包内置 Chromium（用户已选）：`browser_channel`/`POR_BROWSER_CHANNEL` 只对抓取/截图浏览器生效；抓取/截图浏览器的 msedge 优先链不动（视频解码仍需要）。
- 交互登录窗口不再注入 stealth.min.js。
- 登录窗口恢复软件渲染并固定 `--force-device-scale-factor=1`。
- 不改登录证据判定逻辑、不改各平台 login_url（除非诊断日志证明需要）。

**方案要点**：

1. `browser_options.py` 新增 `interactive_login_launch_options`：无 channel、保留 `--disable-gpu`、追加 `--force-device-scale-factor=1`、代理透传。
2. 新建 `src/auth/login_window.py`（`open_login_browser`）：离屏预置 + 一次性 reveal；CDP reveal 失败时丢弃离屏窗口并重开为普通可见窗口（不再抛错阻断登录）；不注入 stealth。
3. 新建 `src/auth/login_window_diagnostics.py`：主框架导航 / pageerror / console error 计数与 reveal 后窗口 bounds 日志（区分刷新循环与纯重绘闪烁）。
4. `window_visibility.reveal_window_once` 加固：先 `windowState:normal` 再定位，失败重试 1 次；`_navigate_login` 增加 `load` + 500ms 稳定等待再 reveal。
5. `service.py` interactive 分支切换到 `open_login_browser`；probe finally 输出诊断汇总。

**涉及模块**：`src/screenshot/browser_options.py`、`src/auth/login_window.py`（新建）、`src/auth/login_window_diagnostics.py`（新建）、`src/auth/service.py`、`src/auth/window_visibility.py`、`src/auth/probe_helpers.py`、`tests/test_auth/test_login_window.py`（新建）、`tests/test_auth/test_window_visibility.py`、`tests/test_auth/test_login_flow.py`、`tests/test_auth/test_service.py`、`tools/smoke_login_window_stability.py`（新建）。

**验收标准**：

- `tests/test_auth` 45 例全绿（含新登录窗口用例 5 例）；全量单测无回归。
- 真机冒烟（**待人工**）：`python tools/smoke_login_window_stability.py` 打开微信公众号登录页静置 90s，主框架导航 ≤2 次、窗口可见可扫码。
- 双机实测（**待人工**）：两台「闪的程度不同」的电脑均完成微信公众号扫码登录；回归抖音登录弹窗、淘宝短信登录正常。

## U10 文字类平台内容页整页长图截图

**优先级**：高（视口截图只留首屏，长文证据不完整，2026-09-30 用户明确要求）

**背景与现状**：

- 自动截图（`PageShooter.capture_named`）与补录截图（`RegionCaptureService`）都只截当前视口；微信公众号/头条等长文正文大部分留在屏外。
- U06 证据铁律：截图必须与最终 URL 同框（真实浏览器地址栏），纯 `page.screenshot(full_page=True)` 丢失浏览器镀铬不可用。

**决策**（2026-09-30 与用户确认）：

- 长图平台=7 个核心文字平台：wechat_official/baijiahao/toutiao/netease_news/sohu_news/ifeng_news/weibo；视频类（图文视频表 10 平台）、其余平台与**作者主页截图**一律不变。
- 方案=**扩窗 + PrintWindow**：定位 HWND 后有界滚动触发懒加载 → 量文档高（封顶 `max_full_page_screenshot_height`，默认 20000）→ SetWindowPos 撑高窗口 → 等重排/图片加载 → PrintWindow 一次抓全（真实标签栏+地址栏在图顶部）→ 恢复原窗口几何。长图失败回退普通视口抓窗。
- 单张 ≤1MB（`long_screenshot_max_bytes`，默认 1_000_000）：长图统一 JPEG，质量阶梯（`long_page_jpeg_quality`=82 起，−8 至 ≥40 末档）→ 宽度缩放阶梯（1.0/0.85/0.7），全超则写最小一档。
- 补录工具条新增「截取长图」按钮，仅长图平台显示（`platform_key` 门控）；失败不终结会话，工具条亮回可重试。
- 超高截图字段恢复 OCR 只识别顶部 4096px（`_TALL_SCREENSHOT_OCR_CROP`），防 RapidOCR 对上万像素长图性能崩塌。

**涉及模块**：`src/screenshot/long_capture.py`（新建：平台集合/扩窗抓取/预算编码/补录处理）、`window_capture.py`（`long_page` 参数）、`page_shooter.py`（`capture()` 门控 + 长图强制 .jpg）、`region_capture.py`+`region_capture_toolbar.py`+`region_capture_helpers.py`（按钮与路由，RegionCaptureResult/confirm 迁入 helpers）、`src/config/settings.py`（默认值 4096→20000 + 新配置）、`src/crawler/ocr_pipeline.py`（顶部裁剪）。

**验收标准**：

- 47 个新用例全绿，全量 877 通过；release-check-ok。
- 真机冒烟（**待人工**）：公众号长文（懒加载图全入图）/微博/头条各一条——地址栏 URL 正确、文件 ≤1MB、内容完整可读；抖音/B站视频与微博主页截图回归不变；补录按钮按平台显隐。
