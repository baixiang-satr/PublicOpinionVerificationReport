"""missing_required_fields 的公众号表规则测试（微信号列=昵称）。"""

from pathlib import Path

from src.crawler.field_quality import missing_required_fields
from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)


def _official_account_record(author_name: str | None) -> RecordResult:
    return RecordResult(
        UrlTask(1, "https://mp.weixin.qq.com/s/abc", "https://mp.weixin.qq.com/s/abc"),
        RecordStatus.ASSETS_READY,
        page=PageData(
            final_url="https://mp.weixin.qq.com/s/abc",
            title="文章标题",
            content_text="正文",
            author_name=author_name,
            author_id=None,
        ),
        route=RouteDecision("公众号", "微信-公众号", "正文"),
        assets=AssetSet(page_screenshot=Path("001.jpg")),
    )


def test_official_account_wechat_id_satisfied_by_nickname() -> None:
    assert missing_required_fields(_official_account_record("邵阳观察")) == []


def test_official_account_wechat_id_missing_when_no_nickname() -> None:
    assert "author_id" in missing_required_fields(_official_account_record(None))
