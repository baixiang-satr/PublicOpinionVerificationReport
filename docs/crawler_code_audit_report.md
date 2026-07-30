# 爬虫代码审计与改进报告

> **文档版本**: v1.0  
> **审计日期**: 2026-07-30  
> **审计范围**: `src/crawler/` 模块（引擎、专用提取器、平台路由、导航、截图管线）

---

## 一、架构总览

当前爬虫管线分为以下几层：

```
CrawlEngine.run()
  └─ PlatformTaskScheduler.queues()     → 按平台分队列
     └─ _process_platform_queue()        → 每队列依次处理
        └─ _process()                    → 单条记录处理
           ├─ navigate_with_fallback()   → 导航 + 访问屏障检测
           ├─ ContentParser.extract()    → 字段提取（专用→目录→通用）
           ├─ PageShooter.capture()      → 正文截图
           ├─ AuthorShooter.capture()    → 作者主页截图
           └─ OcrPipeline.enrich()       → OCR 补充
```

整体设计清晰，分层合理。以下按模块列出审计发现与改进建议。

---

## 二、专用提取器 (`src/crawler/platforms/`) 审计

### 2.1 哔哩哔哩 — 无专用提取器 ⚠️

当前 `registry._DEFAULT_MODULES` 中没有 bilibili 的专用模块。B 站的成功提取完全依赖通用 `CatalogPlatformExtractor` + DOM 选择器。实测 B 站 3/3 成功，说明当前 DOM 选择器工作良好。

**建议**: 可考虑为 B 站增加专用提取器（从 `window.__INITIAL_STATE__` 提取视频数据），获取更精确的发布时间、播放量等字段。目前非必须。

### 2.2 今日头条 — 依赖 bytedance_ssr 混合提取 ⚠️

`bytedance_ssr.py` 同时支持头条（ToutiaoExtractor）和西瓜视频（XiguaExtractor）。

**问题**: 头条的 `_SSR_HYDRATED_DATA` 提取不稳定：
- URL #1 (#7610591062242935322) 完整成功
- URL #2 (#7615974938382205440) 返回 `CONTENT_UNAVAILABLE`

**根因**: 头条的文章 ID 有时效性，SSR 数据可能过期或被清除。

**建议**: 为头条增加从 DOM meta 标签提取的后备路径，当 SSR 数据不存在时仍保证基础字段。

### 2.3 西瓜视频 — 提取器失效 ❌

西瓜视频 3 条 URL 全部失败（`CONTENT_UNAVAILABLE`）。

**根因**: 
- `bytedance_ssr.py` 中的 `XiguaExtractor` 依赖 `_SSR_HYDRATED_DATA`
- 实测西瓜视频页面已不再输出该 SSR 数据，或者 URL 全部失效
- 通用 DOM 选择器也不匹配当前页面结构

**建议**:
1. 检查西瓜视频当前页面结构，更新 `ixigua` 的 DOM 选择器
2. 如果西瓜视频业务已调整（内容迁移），确认该平台在模板中的必要性
3. 考虑添加 `manual_only=True` 标记

### 2.4 抖音 — 提取器有效但访问受限 ❌

`DouyinExtractor` 能解析 RENDER_DATA 脚本，但页面本身需要登录才能渲染内容。

**实测**: 3 条 URL 中 2 条空渲染页、1 条内容不可用。

**建议**:
1. 抖音页面在无登录态下返回空壳，无法通过改进提取器解决
2. 需在 `PlatformDefinition` 中设置更精准的 `include_patterns`，避免无效抓取
3. 考虑在直播/热门场景下找到可公开访问的 URL 形式
4. 长期: 通过登录态解决

### 2.5 快手 — 提取器有效但 JSON 响应问题 ⚠️

`KuaishouExtractor` 处理了 `INIT_STATE` 和 JSON 响应，但：
- 2/3 URL 返回 `UNEXPECTED_API_RESPONSE`
- 1/3 通过平台 fallback（gifshow.com 移动端）成功

**建议**:
1. 当页面直接返回 JSON 时，提取器应作为纯 JSON 处理（无需 DOM），但当前 `content_text` 仍为空
2. 改进 `UNEXPECTED_API_RESPONSE` 的处理逻辑：如果 JSON 中已包含有效字段，应直接返回 `PageData`
3. 快手 fallback 机制已生效，但提取的作者字段丢失（`author_name` 为空）

### 2.6 小红书 — 提取器有效但作者缺失 ⚠️

`XiaohongshuExtractor` 能提取标题、正文和时间，但 author 字段为空。

**问题**: 小红书首页和 Explore 页面的 `__INITIAL_STATE__` 数据结构已变更，`author` 节点不在预期位置。

**建议**:
1. 更新 `_find_note` 方法，适配最新小红书的页面数据结构
2. 检查网络请求中是否包含作者信息的 API 响应
3. 增加 DOM 选择器提取后备（页面可见的作者名）

### 2.7 知乎 — 提取器有效但 403 问题 ❌

`ZhihuExtractor` 能成功从 zhuanlan 文章提取全字段（`/p/102280558` 成功），但问答页面（`/question/`）全部 403。

**根因**: 知乎的 webdriver 检测主动返回 403。

**建议**:
1. 当前 stealth 脚本不足以绕过知乎检测，需要更强的反检测策略
2. 考虑对知乎请求增加更多浏览器指纹伪装
3. 对 `/question/` 路径考虑使用 `manual_only=True`

### 2.8 微博 — 提取器有效但登录墙 ❌

`WeiboExtractor` 能解析 mblog JSON 和 DOM probe，但所有 URL 都需要登录。

**建议**:
1. 确认微博访客模式是否还能使用（之前测试有访客页）
2. 增加微博访客 Cookie 的自动获取逻辑
3. 如需批量抓取微博，必须在 GUI 中完成登录态配置

### 2.9 百度贴吧 — HTTP 403 封锁 ❌

3 条 URL 全部返回 `HTTP_403`，百度统一反爬系统直接封锁。

**建议**:
1. 贴吧的 403 是所有 URL 统一返回，不是 URL 失效问题
2. 和知乎一样，需要更强的反检测脚本
3. 贴吧有 fallback 到 `tieba.baidu.com/mo/q/m?tid=` 移动端路径的逻辑，但本次测试未触发

### 2.10 百度百家号 — 验证码问题 ⚠️

`BaijiahaoExtractor` 能提取 SSR 数据，但部分 URL 触发百度统一验证码。

**建议**:
1. 验证码问题是百度整体反爬策略，无法通过代码绕过
2. 增加 `manual_only=True` 建议，或引导用户通过登录态解决
3. 对于能访问的页面（第1条成功），提取效果良好

### 2.11 搜狐视频 — 提取器效果良好但作者缺失 ⚠️

`SohuVideoExtractor` 的 DOM probe 能提取标题和正文，但作者字段丢失（`author_name` 为 None）。

**建议**:
1. 搜狐视频的页面结构可能已变更，需要更新 `_DOM_PROBE` 中的作者选择器
2. 当前 `author` 选择器 `[class*='user-name']` 等未匹配到实际 DOM
3. 检查网络请求中是否存在上传者信息

### 2.12 微信公众号 — 内容受登录限制 ⚠️

URL 访问后返回空渲染页或截图失败。

**建议**:
1. 微信公众号文章在 PC 端浏览器可直接访问（无需登录），但本机测试中未渲染
2. 可能是 Playwright headless 模式被微信 CDN 识别
3. 建议在非 headless 模式下测试公众号文章

### 2.13 微信视频号 — 已标记手动抓取 ✅

`wechat_video` 的 `PlatformDefinition` 已正确设置 `manual_only=True`，引擎直接跳转人工补录。当前行为符合预期。

### 2.14 网易新闻 — 提取效果良好 ✅

网易新闻两条 URL 完整成功（标题、正文、作者、截图），通用提取器工作良好。

**建议**:
1. 当前网易新闻的成功依赖 URL 有效，建议增加从首页提取文章链接的路径
2. 作者主页 URL 推导可以优化

---

## 三、横切关注点改进

### 3.1 反检测/Stealth 策略

当前 `stealth.py` 只做了基础的 webdriver 抹除和 Chrome runtime 伪装。

**改进建议**:
1. **增加更多指纹伪装**:
   - 添加 `navigator.plugins` 伪装（当前为空数组）
   - 添加 `navigator.hardwareConcurrency` 伪装
   - 添加 WebGL 指纹伪装
   - 添加 `navigator.deviceMemory` 伪装
2. **使用 `playwright-stealth` 插件**: 如果项目中已有 `stealth.min.js`，确认其是否被正确加载（`browser.py` 中 `add_init_script` 路径正确）
3. **动态 User-Agent**: 目前 User-Agent 固定，建议从常见列表轮换

### 3.2 平台 Fallback 策略

`platform_fallbacks.py` 实现了 Hupu、Tieba、Dongchedi、Kuaishou 的 URL fallback。

**改进建议**:
1. **增加更多平台 fallback**: 抖音、微博等可以考虑添加移动端 URL fallback
2. **Fallback 链扩展**: 当前 fallback 只尝试一个备选 URL，可以扩展为多级 fallback

### 3.3 错误处理与重试

当前错误分类清晰，但重试策略较为简单（0 次重试）。

**改进建议**:
1. 区分可重试错误（网络超时、限流 429）与不可重试（403、404、内容不存在）
2. 对 `EMPTY_RENDERED_PAGE` 增加一次带更长等待时间的重试

### 3.4 性能优化

**建议**:
1. 当前 42 条测试总体耗时较长（部分平台单条需要 70s+）
2. 对于 `manual_only` 平台（微信视频号），应在调度阶段直接跳过，不做页面访问
3. 增加资源拦截（广告、统计脚本），减少不必要的网络开销

### 3.5 缺少的平台专用提取器

当前 `_DEFAULT_MODULES` 只注册了 9 个平台的专用提取器。以下平台缺少专用提取器：

| 平台 | 依赖 | 建议 |
|------|------|------|
| **哔哩哔哩** | 通用 DOM + catalog | 可选添加，当前效果已好 |
| **微信公众号** | 通用 DOM + catalog | 可考虑，但主要瓶颈在访问权限 |
| **微信视频号** | 无（manual_only） | 不需要 |
| **网易新闻** | 通用 DOM + catalog | 可选添加，当前效果良好 |
| **微博** | 已存在专用提取器 | ✅ |
| **贴吧** | 已存在专用提取器 | ✅ |
| **今日头条** | bytedance_ssr（共享） | ⚠️ 需要独立改进 |
| **西瓜视频** | bytedance_ssr（共享） | ❌ 需要修复 |

---

## 四、代码质量与健壮性

### 4.1 优点

1. **分层清晰**: 专用提取器、目录提取器、通用提取器三层递减，降级优雅
2. **错误分类完善**: 14+ 种语义化错误码，GUI 可针对性展示
3. **资源管理**: BrowserPool 有完整的生命周期管理，支持取消和清理
4. **超时防护**: 单条记录有硬超时（`page_processing_timeout_seconds`），避免卡死
5. **限速控制**: `HostRateLimiter` 避免对同一域名过于频繁访问

### 4.2 改进点

1. **提取器异常隔离**: 当前提取器异常被 `try/except` 吞掉并回退到通用提取器。建议增加告警日志记录专用提取器失败的频率，便于追踪平台结构变更。

2. **网络 payload 收集的内存风险**: `NetworkPayloadCollector` 将 JSON 响应缓存到内存中，虽然有限制（`max_structured_payload_bytes=2MB`），但某些重 JSON 页面（如抖音）可能产生大量数据。

3. **OCR 不可用**: 当前环境缺少 `rapidocr-onnxruntime`，OCR 功能在后备路径中未生效。提示日志显示 `IMAGE_ONLY_NO_TEXT`，但实际并未执行 OCR。

4. **截图质量校验**: `PageShooter` 实现了空白/纯色检测（`_is_visually_blank`），但阈值可能需要调优，某些平台返回的"登录页截图"（非空白但无内容）未被过滤。

5. **测试覆盖**: 当前 `tests/test_crawler/test_platforms/test_extractors.py` 主要测试本地 fixture，缺少对真实页面结构变化的监控。

---

## 五、优先级建议

### P0 — 立即修复

| 问题 | 影响平台 | 建议操作 |
|------|---------|---------|
| 西瓜视频提取器失效 | 西瓜视频 | 更新/修复 `bytedance_ssr.XiguaExtractor` |
| 小红书作者字段缺失 | 小红书 | 更新 `XiaohongshuExtractor` 适配最新页面结构 |
| Stealth 策略不足 | 知乎、贴吧、百家号 | 增加更多指纹伪装 |

### P1 — 近期改进

| 问题 | 影响平台 | 建议操作 |
|------|---------|---------|
| 快手 JSON 响应处理 | 快手 | 改进 `UNEXPECTED_API_RESPONSE` 路径，从 JSON 直接提取字段 |
| 搜狐视频作者提取 | 搜狐视频 | 更新 `SohuVideoExtractor` 的 DOM 探测选择器 |
| 抖音公开 URL 探索 | 抖音 | 寻找可公开访问的抖音内容 URL 模式 |
| 平台 fallback 扩展 | 微博、抖音等 | 增加移动端 URL fallback |

### P2 — 中长期

| 问题 | 建议操作 |
|------|---------|
| OCR 依赖安装 | 安装 `rapidocr-onnxruntime`，启用纯图片页面文字提取 |
| 网络 payload 内存优化 | 考虑流式处理或磁盘缓存 |
| 自动检测平台结构变更 | 增加提取器成功率监控告警 |
| 动态 User-Agent 池 | 实现 UA 轮换 |
| 首页→文章链接提取 | 对网易、凤凰等平台实现从首页发现文章链接的机制 |

---

## 六、总结

当前爬虫管线架构良好，专用提取器策略正确。核心瓶颈集中在 **访问权限**（登录墙、403 封锁）而非提取逻辑本身。

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐ | 分层清晰、降级优雅、错误分类完善 |
| 专用提取器 | ⭐⭐⭐ | 9/14 平台有专用提取器，但部分需要更新适配 |
| 反爬对抗 | ⭐⭐ | 基础 stealth 不足以绕过知乎/贴吧/百度的检测 |
| 错误处理 | ⭐⭐⭐⭐ | 语义化错误码、访问屏障检测、超时保护完善 |
| 资源管理 | ⭐⭐⭐⭐⭐ | 浏览器池、取消支持、资源清理完整 |
| 可测试性 | ⭐⭐⭐ | 有 fixture 和 mock，但真实平台测试默认跳过 |
