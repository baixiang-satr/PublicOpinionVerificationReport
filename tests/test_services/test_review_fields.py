"""ReviewSession field-display rules: 发布时间空显、公众号表昵称/UIN 规则。"""

from pathlib import Path

from src.domain.models import (
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.services.review_session import ReviewSession


def _record(page: PageData, sheet: str, platform: str) -> RecordResult:
    return RecordResult(
        UrlTask(1, "https://example.test/a", "https://example.test/a"),
        RecordStatus.ASSETS_READY,
        page=page,
        route=RouteDecision(sheet, platform, "正文"),
    )


def _view(session: ReviewSession, evidence_id: int, field: str):
    return next(view for view in session.field_views(evidence_id) if view.field == field)


def test_unparsable_publish_time_displays_blank_instead_of_raw(tmp_path: Path) -> None:
    """抖音异常的 245000 不再回显；发布时间宁可留空待补录。"""

    record = _record(
        PageData(
            title="标题",
            content_text="正文",
            author_name="昵称",
            published_at=None,
            published_at_raw="245000",
        ),
        "图文视频",
        "字节跳动_抖音_图文视频",
    )
    session = ReviewSession.from_records(tmp_path, [record])

    view = _view(session, 1, "published_at")

    assert view.value == ""


def test_official_account_sheet_fills_wechat_id_with_nickname_and_blanks_uin(
    tmp_path: Path,
) -> None:
    """公众号表：微信号(必填)列=公众号昵称；UIN 不采集留空。"""

    record = _record(
        PageData(
            title="文章标题",
            content_text="正文",
            author_name="邵阳观察",
            author_id="gh_fakeid123",
            account_uin="123456789",
        ),
        "公众号",
        "微信-公众号",
    )
    session = ReviewSession.from_records(tmp_path, [record])

    author_id_view = _view(session, 1, "author_id")
    uin_view = _view(session, 1, "account_uin")

    assert author_id_view.value == "邵阳观察"
    assert author_id_view.required
    assert not author_id_view.missing
    assert uin_view.value == ""


def test_official_account_rule_keeps_manual_override_winning(tmp_path: Path) -> None:
    record = _record(
        PageData(title="文章标题", content_text="正文", author_name="邵阳观察"),
        "公众号",
        "百度_百家号_公众号",
    )
    session = ReviewSession.from_records(tmp_path, [record])
    session.set_field(1, "author_id", "人工填写微信号")

    assert _view(session, 1, "author_id").value == "人工填写微信号"
