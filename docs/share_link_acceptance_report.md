# 分享链接爬取专项验收报告

> **验收日期**: 2026-08-05  
> **验收基准**: 用户提供的 23 条社交媒体分享链接（`tests/test_input/social_share_links.csv`）  
> **验收工具**: `tools/test_share_links.py`（全管线实跑：导航→解析→截图→OCR→导出 template.zip）  
> **运行环境**: Windows 11 + .venv + Playwright Chromium（各平台登录态已由用户提前管理）

---

## 一、最终结论

| 指标 | 结果 |
|------|------|
| **完整成功率** | **23/23 = 100%**（达标线 85%，即 ≥20/23） |
| 路由命中率 | 23/23（每平台专用提取器/目录均命中） |
| 内容正确性 | 抽查 12 条与页面实况/已知事实完全一致；全部字段来源可追溯 |
| 离线门禁 | `pytest tests/` 591 passed + 76 skipped；`release_check` → release-check-ok |

**完整成功口径**（与用户约定）：页面可访问（HTTP 200、非登录/风控墙）+ 正文 + 作者 + 有效内容页截图；标题仅当目标工作表有标题列（公众号表）时强制。

## 二、逐平台成绩

| 平台 | 条数 | 成功 | 说明 |
|------|:---:|:---:|------|
| 微信公众号 | 1 | 1/1 | 文章页 DOM + 页面全局 |
| 微信视频号 | 2 | 2/2 | 见「关键攻坚 1」 |
| 小红书 | 3 | 3/3 | 修复作者字段（explore + discovery/item 两种形态） |
| 微博 | 3 | 3/3 | 登录态下移动 JSON 接口 |
| 抖音 | 3 | 3/3 | 含 2 条 `/user/?modal_id=` 形态，见「关键攻坚 4」 |
| 今日头条 | 3 | 3/3 | 含 2 条 `m.toutiao.com/is/` 短链 + 1 条微头条 `/w/` |
| 哔哩哔哩 | 3 | 3/3 | 新建专用提取器（`__INITIAL_STATE__` + BV 号锁定） |
| 西瓜视频 | 3 | 3/3 | 见「关键攻坚 2」 |
| 百度百家号（mbd） | 2 | 2/2 | videolanding + landingsuper 两种落地页 |

## 三、四轮实跑轨迹

| 轮次 | 成功率 | 主要动作 |
|------|--------|----------|
| 第 1 轮 | 16/23（69.6%） | 基线：B站/头条微头条/西瓜/视频号/百家号落地页暴露缺口 |
| 第 2 轮 | 20/23（87.0%） | 西瓜改移动页方案、百家号 jsonData 路径生效 |
| 第 3 轮 | 23/23（100%） | 视频号根因修复（启动参数）+ landingsuper 作者补齐 |
| 最终轮 | 23/23（100%） | 抖音 modal 链接规范化，字段与截图证据一致 |

## 四、关键攻坚记录

### 1. 微信视频号：Chromium 启动参数根因（二分定位）
- **现象**：页面 HTTP 200、JS 加载、API 有响应，但 DOM 恒定空壳，截图闸门连续失败。
- **定位**：镜像 context 正常 → 逐级叠加引擎处理步骤正常 → 唯一差异是启动参数；二分后确认 **`--disable-web-security`** 使 finder-preview SPA 渲染空壳。已从 `ANTI_DETECTION_ARGS` 移除。
- **提取链路**：`finder-preview/api/feed/get_feed_info` 接口（`data.feedInfo.description` + `data.authorInfo.nickname` + `createtime`）；扫码引导弹窗（`.qr-modal-overlay`，无文本按钮）在截图前确定性移除。

### 2. 西瓜视频：PC 站关停，改走移动分享页
- **事实**：`www.ixigua.com/video/{id}` 与首页均经 ttwid 壳跳转 `/app/` 下载页（PC Web 已停止服务）；`m.ixigua.com/dx/{id}` 移动分享页在桌面 UA 下完整 SSR 渲染。
- **实现**：撤销 `/dx/` → PC 的规范化（保持原样直达）；`XiguaExtractor` 新增移动页 DOM 探测（`h1.xigua-feedtitle` / `.xigua-author` / `.xigua-timetag` 的 `YYYY-MM-DD发布` 项）；JSON-LD 标题的「 | 西瓜视频」后缀与样板简介在 finalize 阶段清除。

### 3. 百家号 mbd 落地页
- `videolanding`：数据在 `window.jsonData`（`curVideoMeta` + 根部 `author`）；`_STATE_SCRIPT` 探测已补 `jsonData`。
- `landingsuper`：数据在 `jsonData.bsData.superlanding[0].itemData`（`infoBaiJiaHao` 作者 + `sections` 正文分段）；`nid` 匹配对 `news_`/`sv_` 前缀做归一化；canonical 例外改为跨载荷全局仲裁（推荐文章不再能冒充目标）。

### 4. 抖音 `/user/?modal_id=` 分享形态
- **问题**：个人页弹窗不可靠——实测同链接两次渲染出不同内容（推荐流视频），导致「字段来自目标节点、截图却是别的视频」的证据不一致。
- **修复**：导航前零网络规范化 `canonicalize_share_url()`：`/user/?modal_id={id}` → `/video/{id}` 规范页；`douyin_aweme_id()` 同步支持 modal_id 取 ID。修复后 DOM 文案与 ID 锁定载荷逐字一致（实测同为「邵阳市同城证件…/重庆一指通贸易有限公司」）。

### 5. 路由与短链补齐
- `toutiao` 路由补 `/w/`（微头条）与 `/is/`；`baijiahao` 补 `landingsuper`；短链预解析主机新增 `m.toutiao.com`（`m.ixigua.com/dx/` 经实测为可用内容页，不做预解析）。
- 微头条提取路径：`content + user` 节点（无语义标题，内容入「信息内容」列，符合模板约定）。

## 五、正确性核验抽样（字段 vs 页面实况）

| # | 平台 | 核验结果 |
|---|------|----------|
| 002 | 视频号 | 标题/作者「地理有文化」/日期与截图逐字一致（QR 弹窗已移除） |
| 003 | 视频号 | 「戴华明札记」与 feed 接口数据一致 |
| 010/011 | 抖音 | 规范化后字段与页面内容一致（ID 锁定载荷 = 页面文案） |
| 016 | B 站 | 「小椰子专栏」(mid 30947486) 与 `__INITIAL_STATE__` 一致 |
| 019/020 | 西瓜 | 「立福128」「王秋裤（裤裤）」与移动页作者区块一致 |
| 022 | 百家号 | 「皮蛋问路」与 jsonData.author 一致 |
| 023 | 百家号 | 「十三月魔」与 superlanding infoBaiJiaHao 一致 |

完整逐链接字段 dump 与截图对照清单见 `output/share-link-acceptance/acceptance_report.md`（每次实跑自动重新生成，含人工打勾栏）。

## 六、遗留说明

1. **抖音 011 发布时间留空**：该 aweme 的 `create_time` 异常（裸数字 245000，非时间），已按「宁可空缺不可造假」原则清除，待人工补录。
2. 视频号作者无公开账号 ID 字段，`author_id` 按昵称回退（`nickname_fallback`），属平台数据面限制。
3. 抖音/B 站个别记录的作者主页截图被身份闸门主动拒绝（`AUTHOR_IDENTITY_MISMATCH`：分享链接的个人页与内容作者不一致）——为防错的正确行为，不影响内容页证据。

## 七、复现方式

```powershell
# 离线门禁
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe tools/release_check.py
# 实跑验收（需先完成各平台登录态管理）
.\.venv\Scripts\python.exe tools/test_share_links.py
```
