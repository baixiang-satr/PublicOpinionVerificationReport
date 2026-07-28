# 下一阶段开发说明：字段完整度、图片内容与个人主页证据

## 1. 文档目的

本文档用于指导 `PublicOpinionVerificationReport` 下一阶段编码。目标是在用户已经正确管理登录态的前提下，提高标题、昵称、发布时间、信息内容和个人主页截图的有效覆盖率，同时保证：

- 输入 URL 与输出业务行一一对应；
- 抓到的数据不会在后续解析、摘要或导出阶段丢失；
- 图片型内容能够正确识别或明确标记；
- 登录页、空白页、删除页、错误页不作为有效截图；
- 单条页面、OCR 或导出异常不会卡住整批任务。

## 2. 本阶段不做的事项

- 不重新设计淘宝、天猫、1688、京东、抖音、微博、今日头条的登录流程。
- 不自动发送验证码，不绕过验证码、权限控制或平台风控。
- 不把本次因“未管理登录态”产生的暂停、403、空页作为抓取器回归。
- 不改变固定模板的工作表名称、列顺序和枚举值。

登录态由用户在运行前通过现有“管理平台登录态”功能准备。本阶段只要求抓取、解析、截图和导出代码正确复用已验证登录态。

## 3. 当前实现中需要优先解决的问题

### 3.1 正文被摘要截短

当前 `TemplateRowMapper` 优先导出 `content_summary`，默认摘要长度是 2000 字。即使 `content_text` 已经拿到完整正文，最终 Excel 也可能只写入前 2000 字。

修改要求：

- `PageData.content_text` 始终保存清洗后的完整正文；
- `content_summary` 只用于界面预览和质量报告，不再作为 Excel 首选值；
- Excel 导出优先使用 `content_text`；
- Excel 单元格最多容纳 32767 个字符，导出上限建议设为 32000；
- 超过上限时记录 `CONTENT_TRUNCATED_FOR_EXCEL`，并在诊断文件保存原始字符数；
- 不得无提示截断。

### 3.2 OCR 不可用与图片无文字混为一谈

当前 `src/utils/ocr.py` 在 OCR 引擎不可用时也返回“无文字”。这会把“根本没有执行 OCR”误判为“图片中确实没有文字”。

必须拆分为以下结果：

| OCR 状态 | 含义 | 是否可以判定图片无文字 |
|---|---|---|
| `success` | OCR 成功且识别到文字 | 否 |
| `no_text` | OCR 成功执行，但没有达到阈值的文字 | 是 |
| `unavailable` | Python 3.12 OCR 进程或模型不可用 | 否 |
| `timeout` | OCR 超时 | 否 |
| `failed` | 图片损坏、模型异常或协议异常 | 否 |

只有 `no_text` 才允许写入“纯图片无文字”标记。

### 3.3 个人主页截图覆盖率仍然不足

当前代码只对少数可稳定推导的平台生成个人主页 URL，且主页有效性主要依赖通用 DOM 标记。下一步需要把“发现主页 URL”“验证身份”“选择截图区域”“判断截图有效”拆成独立步骤。

### 3.4 已抓取字段缺少候选仲裁

同一个字段可能同时来自平台接口、内嵌 JSON、JSON-LD、DOM、Meta 和 OCR。当前逻辑偏向“先得到非空值就使用”，容易保留：

- `Prefetch`、`首页`、`商品详情`等假标题；
- 日期加来源组成的假昵称；
- 当前系统时间、抓取时间被误当作发布时间；
- 平台首页介绍被误当作目标正文。

下一步应保留字段候选、来源和置信度，再统一决策。

### 3.5 取消后结果不能直接续跑

抓取引擎已经能快速取消，但正式 `TaskRunner` 仍主要在任务结束后生成完整质量文件。下一步需要把每条结果原子化落盘，使程序崩溃、人工取消或 Excel 导出失败后可以从未完成记录继续。

### 3.6 Excel 导出仍依赖桌面 COM

现有模板文件实际是旧式 OLE Excel 文件但扩展名为 `.xlsx`，正式导出仍依赖 Windows Excel COM。按列批量写入已经降低耗时，但 COM 仍受桌面登录会话、Excel 安装状态和进程残留影响。

下一步应将源模板一次性转换为真正的 Office Open XML `.xlsx`，并建立经过模板契约测试的非 COM 导出路径。

## 4. 图片内容处理规则

### 4.1 新增内容类型

建议在 `PageData` 增加：

```text
content_kind:
  text
  image_with_text
  image_without_text
  mixed_text_and_image
  video_only
  unknown
```

同时增加：

```text
ocr_status
ocr_image_count
ocr_text_image_count
original_content_chars
exported_content_chars
```

建议为系统生成的标记新增 `ExtractionSource.SYSTEM_MARKER`，不得把标记伪装成 OCR 文本。

### 4.2 信息内容决策表

| 页面情况 | 信息内容写法 | 状态/错误码 |
|---|---|---|
| DOM/接口已有正文，无正文图片文字 | 使用完整正文 | 正常 |
| DOM/接口已有正文，图片 OCR 得到补充文字 | 正文后追加 `【图片文字】` 段，去重后写入 | `mixed_text_and_image` |
| 没有正文，存在内容图片，OCR 得到文字 | 使用图片 OCR 文字 | `image_with_text` |
| 没有正文，存在内容图片，OCR 成功但无文字 | 写入 `【纯图片内容：图片中未识别到文字】` | `IMAGE_ONLY_NO_TEXT` |
| 没有正文，存在内容图片，但 OCR 不可用/超时/失败 | 保持内容为空，进入待补录 | `OCR_UNAVAILABLE` / `OCR_TIMEOUT` / `OCR_FAILED` |
| 没有正文，也没有可信内容图片 | 保持内容为空，进入待补录 | `EMPTY_PAGE` |

重要约束：

- “图片中没有文字”标记只能在 OCR 确实成功运行后生成；
- 页面图标、头像、Logo、二维码、广告、表情、跟踪像素不能算正文图片；
- OCR 文本必须按页面图片顺序合并；
- DOM 文本和 OCR 文本需要按标准化后的行进行去重；
- 不因为存在少量 DOM 导航文字就跳过正文图片 OCR。

### 4.3 内容图片筛选

从 `article`、`main`、正文选择器或正文卡片内部收集候选图片，记录其页面顺序和可见尺寸。

默认排除：

- 宽或高小于 80 像素；
- 面积小于 10000 像素；
- `avatar`、`logo`、`icon`、`emoji`、`qrcode`、`banner`、`ad`等类名；
- 透明跟踪图片、占位图和重复 URL；
- 正文区域外的推荐列表图片。

当图片 URL 无法下载、Canvas 渲染或存在跨域限制时，使用元素区域截图作为 OCR 输入。

## 5. OCR 独立 Python 3.12 进程

### 5.1 目标架构

主程序继续运行在 Python 3.14；RapidOCR/ONNX Runtime 只运行在独立 Python 3.12 子进程。

建议新增：

```text
src/ocr/models.py
src/ocr/client.py
src/ocr/protocol.py
src/ocr/worker_main.py
```

职责：

- `models.py`：`OcrRequest`、`OcrImageResult`、`OcrBatchResult`和状态枚举；
- `protocol.py`：JSON Lines 请求/响应格式；
- `client.py`：启动、复用、超时、重启和关闭 OCR 子进程；
- `worker_main.py`：在 Python 3.12 中加载一次 RapidOCR，并处理多批图片。

### 5.2 进程约束

- OCR 模型只在工作进程启动时加载一次；
- 单批 OCR 默认超时 45 秒；
- 子进程退出、协议损坏或超时后最多自动重启一次；
- 取消任务时必须终止当前 OCR 请求，不能阻塞浏览器关闭；
- 标准输出只传 JSON，不输出模型日志；
- 日志写入标准错误或独立文件；
- 不把图片二进制放入 JSON，传递受控的本地绝对路径；
- 每条图片返回文字、置信度、文本框和错误状态；
- OCR Python 路径通过配置指定，不得硬编码用户目录。

建议配置：

```text
POR_OCR_PYTHON_EXECUTABLE
POR_OCR_WORKER_TIMEOUT_SECONDS
POR_OCR_MAX_RESTARTS
POR_OCR_MIN_IMAGE_WIDTH
POR_OCR_MIN_IMAGE_HEIGHT
```

## 6. 平台专用接口与内嵌 JSON 解析

### 6.1 统一适配器接口

建议建立平台适配器层：

```text
src/crawler/platform_adapters/base.py
src/crawler/platform_adapters/<platform>.py
src/crawler/field_resolver.py
```

每个平台适配器输出字段候选，而不是直接覆盖最终值：

```text
field
value
source
confidence
source_path
is_primary_content
```

`source_path` 用于诊断，例如：

```text
network_json.aweme_detail.desc
embedded_json.__INITIAL_STATE__.article.title
json_ld.author.name
platform_dom.article_time
ocr.image_2
```

### 6.2 字段来源优先级

默认优先级：

1. 目标平台正文接口的明确字段；
2. 页面内嵌的目标内容 JSON；
3. JSON-LD；
4. 平台专用 DOM；
5.可信 Meta；
6. OCR；
7. 通用 DOM/可见文本。

字段需要单独校验，不能只按统一顺序取值：

- 标题：拒绝首页、登录、下载、帮助中心、`Prefetch`等壳页面标题；
- 昵称：拒绝日期、来源说明、按钮文字和多行元数据容器；
- 时间：区分发布时间、更新时间和抓取时间；禁止用当前抓取时间填充发布时间；
- 正文：拒绝平台介绍、页脚、登录说明、评论导航和推荐列表；
- 作者 ID：优先稳定 ID，不默认把昵称当 ID；昵称兜底必须在诊断中明确标识。

### 6.3 平台优先级

在登录态由用户准备好的前提下，优先补齐：

P0：

- 抖音电商、抖音图文视频；
- 快手；
- 微信视频号；
- 爱奇艺；
- 百家号；
- 懂车帝；
- QQ 浏览器目标页面。

P1：

- 淘宝、天猫、1688、京东的商品、店铺和作者字段；
- 微博和今日头条；
- 搜狐视频、优酷、土豆的作者主页 URL；
- 网易、凤凰、搜狐生活资讯的作者名称清洗。

每个平台至少保存一组脱敏的 HTML/内嵌 JSON/接口 JSON 测试夹具，使解析器可以离线回归，不依赖实时网站。

测试夹具严禁包含 Cookie、Token、手机号、账号密码或其他登录凭据。

## 7. 个人主页截图专项

### 7.1 合理统计口径

不是每个 URL 都存在个人主页，例如浏览器官网、帮助中心和平台规则页。主页截图覆盖率应按“可识别作者且平台存在网页主页”的记录计算，不应使用全部 68 条作为分母。

建议输出：

```text
author_profile_eligible
author_profile_url_found
author_profile_accessible
author_profile_screenshot_valid
author_profile_failure_code
```

### 7.2 主页 URL 发现顺序

1. 正文接口返回的官方作者主页 URL；
2. 页面内嵌 JSON 中的作者 URL；
3. 正文 DOM 的作者链接；
4. 使用稳定作者 ID 推导官方 URL；
5. 输入 URL 本身就是个人主页时直接使用。

需要逐步补充的稳定规则包括：

- 抖音 `sec_uid`；
- 快手 `userId` / `eid`；
- 小红书用户 ID；
- B站 `mid`；
- 微博 UID；
- 微信公众号 `__biz`；
- 百家号作者 ID；
- 搜狐、网易、凤凰、虎扑、美团等平台的作者路径或账号 ID。

只有官方域名和明确的路径模式可以进入截图流程。昵称搜索结果页不能作为个人主页。

### 7.3 身份一致性验证

打开主页后验证：

- 最终 URL 没有跳到登录页、首页、错误页或搜索页；
- 页面存在头像、昵称、账号 ID、粉丝数、简介或作品列表中的至少两类；
- 如果正文已提取作者昵称/ID，主页身份应与其一致；
- 不一致时返回 `AUTHOR_IDENTITY_MISMATCH`，不生成附件；
- 页面已删除、用户不存在或参数错误时不生成附件。

### 7.4 截图区域

主页截图应优先包含：

- 头像；
- 昵称；
- 账号 ID 或认证信息；
- 粉丝/关注等身份信息；
- 第一屏作品或动态。

不默认截取无限长主页。建议优先截取“身份头部 + 第一屏内容”，最大高度沿用 4096 像素。

对于横向拼接或超宽页面：

- 根据昵称、头像、`profile`、`user-info`、`author-info`等元素计算目标区域；
- 使用目标区域中心确定横向 `clip.x`；
- 如果一侧主要是登录二维码/登录表单，另一侧是有效主页，只保留有效主页侧；
- DOM 无法定位时，再使用图像列分段和 OCR 登录关键词进行二次裁剪；
- 裁剪后重新执行空白、登录、删除和身份一致性检查。

### 7.5 图片型个人主页

如果个人主页主要以图片呈现，但官方 URL、头像区域和页面结构可以确认身份，允许保留截图，并标记 `AUTHOR_PROFILE_IMAGE_DOMINANT`。

如果页面既无可识别文字，也无可靠身份结构，则不应把普通图片墙认作个人主页。

## 8. 截图质量判定

正文截图和主页截图统一经过以下检查：

1. 文件存在且可解码；
2. 不是空白或近似单色；
3. 页面不是纯登录、验证码、删除、404、权限错误；
4. 页面包含目标正文或身份区域；
5. 没有明显的横向错误拼接；
6. 最终 URL 与目标类型一致；
7. 截图对应的文件名只在全部检查通过后写入 Excel。

建议新增：

```text
src/screenshot/content_region.py
src/screenshot/visual_validator.py
src/screenshot/profile_validator.py
```

截图被拒绝时保存原因到诊断文件，但不把拒绝的图片复制进最终 ZIP。

## 9. 断点保存、续跑与子集重试

建议新增 `src/services/checkpoint_store.py`。

每完成一条记录立即原子写入：

```text
job_state.json
diagnostic_records.partial.json
```

规则：

- 先写临时文件，再原子替换正式文件；
- 保存输入文件指纹、URL 数量、证据编号和配置摘要；
- 续跑时校验输入指纹，防止把旧结果错误套到新输入；
- 已完成且资产仍有效的记录直接复用；
- 只重跑 `needs_review`、`failed`、`cancelled`或用户选中的证据编号；
- 登录态变化后允许按平台重试；
- OCR 修复上线后允许只重跑缺少内容/时间/昵称的记录；
- Excel 导出失败时不重新抓网页，直接从 checkpoint 重新导出。

取消任务后应保留 checkpoint、质量报告和待补录清单；是否生成“部分 ZIP”由界面单独选择，默认不生成。

## 10. 一一对应和导出保证

### 10.1 行数保证

- 所有已被平台目录支持的输入 URL 都必须生成业务行；
- 失败记录至少写入原始 URL、发布平台和文本类型；
- 工作表内按 `evidence_id` 排序；
- 同一个 URL 重复输入时仍保留多个独立证据编号；
- 跳转后的 URL 只写入诊断文件，Excel 保留原始输入 URL；
- 路由失败不得静默丢行，应在任务启动前提示“不属于固定模板平台”，或写入明确的外部待处理清单。

### 10.2 非 COM 导出

实施顺序：

1. 将源模板转换为真正的 `.xlsx`；
2. 建立模板 SHA-256 和工作表契约测试；
3. 使用非 COM 写入器修改业务单元格；
4. 验证保护、下拉枚举、日期格式、列宽和工作表顺序；
5. 保留 COM 写入器作为短期兼容回退；
6. 非 COM 路径稳定后移除运行时 Excel 依赖。

导出失败不得删除抓取 checkpoint。

## 11. 质量报告和界面信息

质量报告增加：

- 标题、昵称、时间、正文的独立覆盖率；
- 完整正文字符数和导出字符数；
- OCR 状态和 OCR 图片数量；
- `content_kind` 分布；
- 个人主页可截图记录数、成功数和失败原因；
- 正文截图拒绝原因；
- 各字段最终来源和置信度；
- 按错误码可重试的证据编号列表。

`pending_manual_entry.csv` 增加：

```text
缺失字段
内容类型
OCR状态
主页截图状态
是否可自动重试
建议重试方式
```

界面详情面板应显示“字段来源”，例如：

```text
标题：network_json
昵称：platform_dom
发布时间：embedded_json
信息内容：ocr（3 张图片）
主页截图：未生成（AUTHOR_ACCESS_RESTRICTED）
```

## 12. 推荐开发顺序

### P0：防止已抓取数据丢失

1. Excel 导出改为优先完整 `content_text`；
2. 增加 32000 字上限和明确截断码；
3. 拆分 OCR 状态，禁止把 OCR 不可用标成图片无文字；
4. 增加 `content_kind` 和纯图片无文字标记；
5. 增加逐条 checkpoint。

### P1：提高内容字段覆盖

1. 实现 Python 3.12 OCR 工作进程；
2. 实现正文图片筛选和元素截图兜底；
3. 实现字段候选与置信度仲裁；
4. 按 P0 平台顺序实现专用适配器；
5. 加强发布时间和昵称清洗。

### P2：个人主页截图专项

1. 扩充稳定主页 URL 推导；
2. 增加身份一致性验证；
3. 实现主页目标区域截图；
4. 实现横向拼接二次裁剪；
5. 增加主页覆盖率和失败原因报告。

### P3：稳定导出和快速复验

1. 真 `.xlsx` 模板；
2. 非 COM 写入；
3. 从 checkpoint 重新导出；
4. 按平台、错误码、缺失字段重跑子集；
5. 形成 68 URL 固定回归基线。

## 13. 测试要求

至少增加以下自动测试：

1. 页面只有图片且图片有文字：Excel 信息内容等于 OCR 文字；
2. 页面只有图片且图片无文字：写入固定标记；
3. OCR 不可用：不得写入“图片无文字”标记；
4. 混合正文和图片文字：合并且去重；
5. 正文超过 2000 字：Excel 仍保留到 32000 字；
6. 正文超过 32000 字：明确记录截断；
7. 假标题、假昵称和当前抓取时间被拒绝；
8. 主页有效：生成 `NNN主页.jpg`；
9. 主页跳登录、首页、404或删除页：不生成附件；
10. 主页身份与正文作者不一致：不生成附件；
11. 左侧登录、右侧正常主页：只保留右侧；
12. OCR 执行中取消：规定时间内退出；
13. 导出失败后从 checkpoint 重试，不重新抓取；
14. 68 条输入始终对应 68 行；
15. 重复 URL 不被合并；
16. ZIP 中不存在未被工作簿引用的截图。

## 14. 回归验收标准

使用同一份 68 URL 输入，并在用户已管理所需登录态后验收：

- 输入对应行：68/68；
- 取消按钮：点击后 5 秒内界面响应，15 秒内结束浏览器/OCR活动；
- `storage_state: Connection closed`和连续 `socket.send()`：0 次；
- 无效登录/空白/删除截图进入 ZIP：0 张；
- 图片有文字的纯图片样本：100% 写入 OCR 文字；
- 图片无文字的纯图片样本：100% 写入固定标记；
- OCR 未运行却被标成图片无文字：0 条；
- 可访问且已发现官方主页 URL 的记录，主页截图有效率目标不低于 80%；
- 标题、昵称、发布时间和正文按“页面实际存在该字段”的可提取样本统计，不按全部 68 条强行计算；
- 每个字段都能在诊断文件追溯最终来源；
- Excel 内容不再被无提示限制为 2000 字；
- Excel 或打包失败后可直接从 checkpoint 重新导出。

## 15. 预计修改文件

现有文件：

```text
src/domain/models.py
src/config/settings.py
src/crawler/engine.py
src/crawler/content_parser.py
src/crawler/structured_data.py
src/crawler/author_profile_urls.py
src/crawler/field_quality.py
src/screenshot/asset_collector.py
src/screenshot/page_shooter.py
src/screenshot/author_shooter.py
src/screenshot/author_asset.py
src/export/row_mapper.py
src/export/excel_writer.py
src/services/task_runner.py
src/tools/quality_report.py
src/tools/crawl_tracker.py
src/ui/widgets/result_table.py
```

建议新增：

```text
src/ocr/models.py
src/ocr/protocol.py
src/ocr/client.py
src/ocr/worker_main.py
src/crawler/content_classifier.py
src/crawler/content_images.py
src/crawler/field_resolver.py
src/crawler/platform_adapters/
src/screenshot/content_region.py
src/screenshot/visual_validator.py
src/screenshot/profile_validator.py
src/services/checkpoint_store.py
```

每个新模块继续遵守单文件不超过 500 行的项目约束。

## 16. 最终定义

本阶段“成功”不应仅指页面返回 HTTP 200 或生成了截图，而应同时满足：

- 行没有丢失；
- 字段尽可能完整并且来源可信；
- 图片型内容被识别或被准确标记；
- 截图能证明目标正文或目标个人主页；
- 所有缺失和失败都有明确原因；
- 取消、崩溃、OCR失败或导出失败后仍能恢复工作。
