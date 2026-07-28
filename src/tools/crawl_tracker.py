"""
临时功能 — 爬取运行报告生成器
================================

⚠️ 本模块为临时功能，项目全部完成后需删除。删除时请一并移除：
   - src/services/task_runner.py 中对本模块的 import 和调用
   - 本文件

功能：每次运行爬取任务后，将结果追加到 output/crawl_run_report.md，
     记录哪些网页爬取成功、哪些失败、失败原因及解决建议。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.domain.models import RecordResult, RecordStatus
from src.utils.time_utils import DEFAULT_TIMEZONE

REPORT_FILE = Path(__file__).resolve().parents[2] / "output" / "crawl_run_report.md"

# ── 失败原因与解决建议对照表 ──────────────────────────────────────────────
_FAILURE_ADVICE: dict[str, str] = {
    # 验证码
    "CAPTCHA_REQUIRED": (
        "遇到验证码拦截。请在运行时**取消勾选「后台运行浏览器」**，"
        "在弹出浏览器窗口中手动完成验证码后，工具会自动继续。"
    ),
    # 登录
    "LOGIN_REQUIRED": (
        "页面需要登录才能访问。请先使用 Playwright 导出登录态 JSON 文件，"
        "然后在「登录态文件」中选择该文件；或取消「后台运行」手动登录。"
    ),
    # 风控 / 访问限制
    "ACCESS_CHALLENGE": (
        "平台风控拦截了本次访问。建议：\n"
        "1. 降低「同时处理几个页面」的并发数（如设为 1~2）；\n"
        "2. 增大「单个页面最长等待时间」；\n"
        "3. 使用代理 IP；\n"
        "4. 稍后重试。"
    ),
    "HTTP_403": "服务器返回 403 禁止访问，可能需要登录态或已被平台反爬拦截。",
    "HTTP_405_ACCESS_RESTRICTED": "页面拒绝当前访问方式（HTTP 405），请核对真实内容 URL。",
    "HTTP_404": "页面不存在（HTTP 404），请确认 URL 是否正确、内容是否已被删除。",
    "CONTENT_NOT_FOUND": "内容不存在或已删除，请核对原始 URL。",
    "HTTP_429": (
        "请求过于频繁，已被限流。建议增大「同时处理几个页面」的间隔时间，"
        "或降低并发数。"
    ),
    "HTTP_5XX": "目标服务器暂时不可用（HTTP 5xx），可稍后重试。",
    # 内容不可用
    "CONTENT_UNAVAILABLE": "平台明确提示内容不存在、已删除或已下线，请核对原始 URL。",
    "CONTENT_REDIRECTED_TO_HOME": "内容链接被重定向到平台首页，原内容可能已失效或被删除。",
    # JavaScript
    "JAVASCRIPT_RENDER_BLOCKED": (
        "页面要求 JavaScript 但正文未渲染。可增大「单个页面最长等待时间」后重试。"
    ),
    # API
    "UNEXPECTED_API_RESPONSE": "页面返回了 JSON 数据而非正常 HTML，请提供浏览器可打开的内容页 URL。",
    # 空页面
    "EMPTY_RENDERED_PAGE": (
        "页面渲染后仍为空。可能需要：\n"
        "1. 提供登录态 JSON 文件；\n"
        "2. 取消「后台运行」，在可视浏览器中查看；\n"
        "3. 增大稳定等待时间。"
    ),
    # 导航超时 / 失败
    "NAVIGATION_TIMEOUT": "页面加载超时，可增大「单个页面最长等待时间」参数后重试。",
    "NAVIGATION_PARTIAL_TIMEOUT": "页面加载部分超时，但已取得可读内容，不影响导出。",
    "NAVIGATION_FAILED": "页面导航失败，请检查 URL 是否正确、网络是否连通。",
    # 解析
    "PARSE_FAILED": "解析页面内容时出错，可能页面结构有变化。",
    "EMPTY_PAGE": "页面没有可审计的标题或正文内容。",
    # 路由
    "ROUTE_UNSUPPORTED": "该 URL 不属于模板支持的平台，无法路由到对应工作表。",
    # 截图
    "PAGE_SCREENSHOT_FAILED": "页面截图失败，可能是页面未完整加载。",
    "AUTHOR_SCREENSHOT_FAILED": "作者主页截图失败。",
    "AUTHOR_ACCESS_RESTRICTED": "作者主页要求登录或验证，无法截图。",
    # OCR
    "OCR_NO_TEXT": "图片 OCR 未识别到文字，可能图片不含文字或清晰度不够。",
    # 模板导出
    "TEMPLATE_CAPACITY_EXCEEDED": "对应工作表已写满，无法写入更多记录。",
    "ROW_MAPPING_FAILED": "记录无法映射到模板列，请联系开发者检查。",
    "PARTIAL_FIELDS_MISSING": "部分字段缺失但已按现有内容导出，不影响交付。",
    # 通用
    "CANCELLED": "任务已被用户取消。",
    "UNEXPECTED": "发生未知错误，请查看运行日志获取详情。",
}


def get_failure_advice(code: str) -> str:
    """返回针对错误代码的中文解决建议。"""
    return _FAILURE_ADVICE.get(code, "请检查网络连接和 URL 是否正确，或稍后重试。如有疑问请联系开发者。")


def generate_run_report(
    records: list[RecordResult],
    job_id: str,
    label: str,
    rejected_count: int,
) -> str:
    """生成单次运行的 Markdown 报告段落。"""
    now = datetime.now(DEFAULT_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

    # 统计
    exported = sum(1 for r in records if r.status in {
        RecordStatus.EXPORTED, RecordStatus.ASSETS_READY,
        RecordStatus.READY_FOR_EXPORT, RecordStatus.CRAWLED, RecordStatus.ROUTED,
    })
    needs_review = sum(1 for r in records if r.status == RecordStatus.NEEDS_REVIEW)
    failed = sum(1 for r in records if r.status == RecordStatus.FAILED)
    cancelled = sum(1 for r in records if r.status == RecordStatus.CANCELLED)
    pending = sum(1 for r in records if r.status == RecordStatus.PENDING)

    lines = [
        f"\n\n---\n",
        f"## 运行报告：{now}\n",
        f"| 项目 | 数值 |",
        f"|------|------|",
        f"| **任务名称** | {label} |",
        f"| **任务 ID** | {job_id} |",
        f"| **处理时间** | {now} |",
        f"| **处理总数** | {len(records)} |",
        f"| **成功导出** | {exported} |",
        f"| **待人工确认** | {needs_review} |",
        f"| **失败** | {failed} |",
        f"| **已取消** | {cancelled} |",
        f"| **未处理** | {pending} |",
        f"| **无效 URL 数** | {rejected_count} |",
        "",
    ]

    if not records:
        lines.append("*(本次运行无记录)*")
        return "\n".join(lines)

    # 成功列表
    successful = [r for r in records if r.status in {
        RecordStatus.EXPORTED, RecordStatus.ASSETS_READY,
        RecordStatus.READY_FOR_EXPORT, RecordStatus.CRAWLED, RecordStatus.ROUTED,
    }]
    if successful:
        lines.append(f"\n### ✅ 成功记录（{len(successful)} 条）\n")
        lines.append("| 编号 | 原始 URL | 最终 URL | 标题 | 平台 |")
        lines.append("|------|----------|----------|------|------|")
        for r in successful:
            orig_url = r.task.original_url[:80]
            final_url = (r.page.final_url or "")[:80]
            title = (r.page.title or "(无标题)")[:50]
            platform = r.route.platform_value if r.route else "未匹配"
            lines.append(f"| {r.task.evidence_id:03d} | {orig_url} | {final_url} | {title} | {platform} |")

    # 失败/待确认列表
    problem_records = [r for r in records if r.status in {
        RecordStatus.FAILED, RecordStatus.NEEDS_REVIEW, RecordStatus.CANCELLED,
    }]
    if problem_records:
        lines.append(f"\n### ❌ 失败 / 待确认记录（{len(problem_records)} 条）\n")
        lines.append("| 编号 | 原始 URL | 状态 | 错误详情 | 可能原因 | 解决建议 |")
        lines.append("|------|----------|------|----------|----------|----------|")
        for r in problem_records:
            orig_url = r.task.original_url[:70]
            status_display = {
                RecordStatus.FAILED: "❌ 失败",
                RecordStatus.NEEDS_REVIEW: "⚠ 待补录",
                RecordStatus.CANCELLED: "– 已取消",
            }.get(r.status, r.status.value)

            # 收集所有错误
            error_codes: list[str] = []
            error_messages: list[str] = []
            suggestions: list[str] = []
            for error in r.errors:
                error_codes.append(error.code)
                error_messages.append(f"{error.code}: {error.message}")
                suggestions.append(get_failure_advice(error.code))

            # 如果没有具体错误，给通用建议
            if not error_messages:
                if r.status == RecordStatus.CANCELLED:
                    error_messages.append("用户取消了任务")
                    suggestions.append("无需操作。")
                elif r.status == RecordStatus.NEEDS_REVIEW:
                    error_messages.append("需要人工确认")
                    suggestions.append("请查看截图后手动判断。")
                else:
                    error_messages.append("未知原因")
                    suggestions.append("请查看运行日志。")

            error_detail = "; ".join(error_messages)
            # 取第一个错误代码对应的建议（最相关）
            primary_advice = suggestions[0] if suggestions else "请查看运行日志。"

            # 可能原因
            possible_causes = {
                "CAPTCHA_REQUIRED": "平台触发了验证码",
                "LOGIN_REQUIRED": "页面需要登录",
                "ACCESS_CHALLENGE": "平台风控/反爬拦截",
                "HTTP_403": "无访问权限",
                "HTTP_404": "页面不存在或已删除",
                "HTTP_429": "请求频率过高被限流",
                "HTTP_5": "服务器错误",
                "CONTENT_NOT_FOUND": "内容已不存在",
                "CONTENT_UNAVAILABLE": "内容已下线",
                "CONTENT_REDIRECTED_TO_HOME": "链接已失效",
                "NAVIGATION_TIMEOUT": "页面加载超时",
                "ROUTE_UNSUPPORTED": "平台不在支持列表中",
                "EMPTY_PAGE": "页面内容为空",
                "CANCELLED": "用户取消",
            }
            cause = possible_causes.get(error_codes[0]) if error_codes else "请查看错误详情"

            lines.append(
                f"| {r.task.evidence_id:03d} "
                f"| {orig_url} "
                f"| {status_display} "
                f"| {error_detail[:100]} "
                f"| {cause} "
                f"| {primary_advice[:100]} |"
            )

    # 添加详细错误信息附录
    if problem_records:
        lines.append("\n### 📋 失败详细诊断\n")
        for r in problem_records:
            lines.append(f"#### 记录 #{r.task.evidence_id:03d}")
            lines.append(f"- **原始 URL**：{r.task.original_url}")
            if r.page.final_url:
                lines.append(f"- **最终 URL**：{r.page.final_url}")
            lines.append(f"- **状态**：{r.status.value}")
            if r.errors:
                lines.append("- **错误列表**：")
                for error in r.errors:
                    advice = get_failure_advice(error.code)
                    lines.append(f"  - `{error.code}`：{error.message}")
                    lines.append(f"    - 💡 **建议**：{advice}")
            lines.append("")

    return "\n".join(lines)


def append_run_report(
    records: list[RecordResult],
    job_id: str,
    label: str = "批量抓取",
    rejected_count: int = 0,
) -> Path:
    """将本次运行结果追加到报告文件中。

    每次调用会在 output/crawl_run_report.md 中追加一个运行报告段落。
    文件不存在时会自动创建并写入标题。

    参数
    ----
    records:
        本次任务的所有记录。
    job_id:
        任务唯一标识。
    label:
        任务标签（显示用）。
    rejected_count:
        因无效 URL 被忽略的数量。

    返回
    ----
    Path
        报告文件的路径。
    """
    report = generate_run_report(records, job_id, label, rejected_count)

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if REPORT_FILE.exists():
        mode = "a"
    else:
        mode = "w"
        header = "# 爬取运行报告\n\n> ⚠️ **临时文件** — 项目完成后将删除此功能。\n\n每次运行的结果将追加到本文档末尾。每个运行段落包含成功/失败记录列表、失败原因及解决建议。\n"
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(header)

    with open(REPORT_FILE, mode, encoding="utf-8") as f:
        f.write(report)

    return REPORT_FILE
