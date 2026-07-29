"""Regression tests for the author-home evidence acceptance pipeline."""

from pathlib import Path

import pytest

from src.domain.models import TemplateRow
from src.export.staging_assets import audit_staged_author_assets
from src.screenshot.author_evidence import (
    AuthorEvidenceDecision,
    ProfilePageType,
    classify_profile_page,
    dismiss_profile_overlays,
    identity_verdict,
    read_decision,
    write_decision,
)


# ── Page type classification ─────────────────────────────────────────────


def test_article_page_is_not_a_profile() -> None:
    page_type = classify_profile_page(
        url="https://www.163.com/dy/article/KS0L7KJR0556F4M0.html",
        title="网易新闻",
        body_text="网易首页 新闻 体育 正文内容" * 20,
        has_profile_surface=False,
        detected_name="新华社",
    )

    assert page_type == ProfilePageType.ARTICLE_PAGE


def test_corporate_csr_section_is_rejected_as_profile() -> None:
    # Regression for evidence 057/058: meituan CSR column, not an author home.
    page_type = classify_profile_page(
        url="https://www.meituan.com/csr/social-responsibility",
        title="美团企业社会责任",
        body_text="企业社会责任 美团公益 社会责任报告" * 10,
        has_profile_surface=False,
    )

    assert page_type == ProfilePageType.CORPORATE_SECTION


def test_personal_space_is_a_profile() -> None:
    page_type = classify_profile_page(
        url="https://space.bilibili.com/12345678",
        title="热血之刃的个人空间",
        body_text="热血之刃 粉丝 关注 作品列表" * 10,
        has_profile_surface=True,
        detected_name="热血之刃",
    )

    assert page_type == ProfilePageType.PERSON_PROFILE


def test_media_account_is_a_media_profile() -> None:
    page_type = classify_profile_page(
        url="https://weibo.com/u/2803301701",
        title="新华社的微博",
        body_text="新华社 官方微博 粉丝" * 10,
        has_profile_surface=True,
        detected_name="新华社",
    )

    assert page_type == ProfilePageType.MEDIA_PROFILE


def test_login_wall_is_login_or_challenge() -> None:
    page_type = classify_profile_page(
        url="https://example.test/user/42",
        title="登录",
        body_text="扫码登录后查看完整主页",
        has_profile_surface=False,
    )

    assert page_type == ProfilePageType.LOGIN_OR_CHALLENGE


def test_deleted_page_is_deleted_or_empty() -> None:
    page_type = classify_profile_page(
        url="https://example.test/user/gone",
        title="提示",
        body_text="用户不存在",
        has_profile_surface=False,
    )

    assert page_type == ProfilePageType.DELETED_OR_EMPTY


def test_shop_page_is_a_store_profile() -> None:
    page_type = classify_profile_page(
        url="https://shop123.taobao.com/shop/view_shop.htm",
        title="旗舰店铺",
        body_text="店铺 宝贝 分类 旗舰店铺" * 10,
        has_profile_surface=True,
    )

    assert page_type == ProfilePageType.STORE_PROFILE


# ── Identity validation (real regression samples) ────────────────────────


def test_identity_rejects_header_naming_somebody_else_047() -> None:
    state, rejection = identity_verdict(
        expected_name="网易",
        expected_id=None,
        detected_name="新华社",
        detected_id=None,
        body_text="网易首页 新闻 体育 新华社 粉丝 简介",
    )

    assert state == "mismatch"
    assert rejection == "AUTHOR_IDENTITY_MISMATCH"


def test_identity_rejects_comment_user_that_is_not_the_author_050() -> None:
    state, rejection = identity_verdict(
        expected_name="第一现场",
        expected_id=None,
        detected_name="热血之刃",
        detected_id=None,
        body_text="热血之刃 评论 回复 第一现场",
    )

    assert state == "mismatch"
    assert rejection == "AUTHOR_IDENTITY_MISMATCH"


def test_identity_rejects_media_header_for_personal_author_051() -> None:
    state, rejection = identity_verdict(
        expected_name="壮壮科普",
        expected_id=None,
        detected_name="观察者网",
        detected_id=None,
        body_text="观察者网 壮壮科普 专栏 文章",
    )

    assert state == "mismatch"
    assert rejection == "AUTHOR_IDENTITY_MISMATCH"


def test_identity_accepts_matching_header_052() -> None:
    state, rejection = identity_verdict(
        expected_name="热血之刃",
        expected_id=None,
        detected_name="热血之刃",
        detected_id=None,
        body_text="热血之刃 粉丝 作品",
    )

    assert state == "verified"
    assert rejection is None


def test_identity_url_carrying_expected_id_is_strong_evidence_025() -> None:
    # Regression for run 20260729-183517 evidence 025: the candidate URL is
    # the author's own space, but a greedy header-ID probe picked up a
    # recommendation link's ID and caused a false mismatch.
    state, rejection = identity_verdict(
        expected_name="一只芋泥小学",
        expected_id="1054492726",
        detected_name="一只芋泥小学",
        detected_id="3546768392325656",
        body_text="一只芋泥小学 粉丝 关注",
        page_url="https://space.bilibili.com/1054492726",
    )

    assert state == "verified"
    assert rejection is None


def test_corporate_path_with_profile_segment_is_rejected_057() -> None:
    # Regression: /csr/people/... contains a profile-looking path segment but
    # is a company CSR column, never an author home.
    page_type = classify_profile_page(
        url="https://www.meituan.com/csr/people/user-rights",
        title="用户权益",
        body_text="便利用户生活 社会责任 用户权益" * 10,
        has_profile_surface=True,
        detected_name="便利用户生活",
    )

    assert page_type == ProfilePageType.CORPORATE_SECTION


def test_identity_id_match_outranks_nickname_difference() -> None:
    state, rejection = identity_verdict(
        expected_name="旧昵称",
        expected_id="12345678",
        detected_name="新昵称",
        detected_id="12345678",
        body_text="新昵称 作品列表",
    )

    assert state == "verified"
    assert rejection is None


def test_identity_id_mismatch_rejects_even_with_matching_name() -> None:
    state, rejection = identity_verdict(
        expected_name="同名账号",
        expected_id="12345678",
        detected_name="同名账号",
        detected_id="87654321",
        body_text="同名账号 作品列表",
    )

    assert state == "mismatch"
    assert rejection == "AUTHOR_IDENTITY_MISMATCH"


def test_identity_without_expected_identity_is_unverified() -> None:
    state, rejection = identity_verdict(
        expected_name=None,
        expected_id=None,
        detected_name="某账号",
        detected_id=None,
        body_text="某账号 作品列表",
    )

    assert state == "unverified"
    assert rejection == "AUTHOR_IDENTITY_UNVERIFIED"


def test_identity_body_match_allowed_when_header_absent() -> None:
    state, rejection = identity_verdict(
        expected_name="某作者",
        expected_id=None,
        detected_name=None,
        detected_id=None,
        body_text="欢迎来到某作者的主页，这里有他的作品列表",
    )

    assert state == "verified"
    assert rejection is None


def test_identity_allows_reasonable_prefix_suffix_difference() -> None:
    state, rejection = identity_verdict(
        expected_name="壮壮科普",
        expected_id=None,
        detected_name="壮壮科普官方账号",
        detected_id=None,
        body_text="",
    )

    assert state == "verified"
    assert rejection is None


# ── Overlay dismissal ────────────────────────────────────────────────────


class _FakeOverlayPage:
    def __init__(self, measurements: list[dict]) -> None:
        self._measurements = measurements
        self.dismissed = False

    async def evaluate(self, script: str) -> object:
        if "clicked" in script:
            self.dismissed = True
            return 1
        if self._measurements:
            return self._measurements.pop(0)
        return {"coverage": 0.0, "loginOverlay": False}

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


@pytest.mark.asyncio
async def test_overlay_clear_when_nothing_blocks() -> None:
    page = _FakeOverlayPage([{"coverage": 0.05, "loginOverlay": False}])

    assert await dismiss_profile_overlays(page) == "clear"
    assert not page.dismissed


@pytest.mark.asyncio
async def test_overlay_dismissed_after_clicking_semantic_buttons() -> None:
    page = _FakeOverlayPage(
        [
            {"coverage": 0.4, "loginOverlay": False},
            {"coverage": 0.02, "loginOverlay": False},
        ]
    )

    assert await dismiss_profile_overlays(page) == "dismissed"
    assert page.dismissed


@pytest.mark.asyncio
async def test_overlay_blocked_when_login_prompt_survives() -> None:
    # Regression for evidence 025/026: identity passes but the login popup
    # cannot be closed, so no screenshot may ship.
    page = _FakeOverlayPage(
        [
            {"coverage": 0.5, "loginOverlay": True},
            {"coverage": 0.4, "loginOverlay": True},
        ]
    )

    assert await dismiss_profile_overlays(page) == "blocked"


@pytest.mark.asyncio
async def test_overlay_blocked_when_coverage_stays_above_threshold() -> None:
    page = _FakeOverlayPage(
        [
            {"coverage": 0.3, "loginOverlay": False},
            {"coverage": 0.2, "loginOverlay": False},
        ]
    )

    assert await dismiss_profile_overlays(page) == "blocked"


# ── Decision persistence and the pre-ZIP audit ───────────────────────────


def _decision(evidence_id: int, accepted: bool, rejection: str | None = None):
    return AuthorEvidenceDecision(
        candidate_url="https://example.test/user/1",
        evidence_id=evidence_id,
        page_type=ProfilePageType.PERSON_PROFILE.value,
        access_state="accessible",
        overlay_state="clear",
        identity_state="verified" if accepted else "unverified",
        accepted=accepted,
        rejection_code=rejection,
    )


def test_decision_roundtrip(tmp_path: Path) -> None:
    path = write_decision(_decision(25, True), tmp_path)

    loaded = read_decision(path)

    assert loaded is not None
    assert loaded.evidence_id == 25
    assert loaded.accepted
    assert path.name == "025主页.decision.json"


def _row(evidence_id: int, attachments: tuple[str, ...]) -> TemplateRow:
    values = {"附件": ",".join(attachments)} if attachments else {}
    return TemplateRow("图文视频", evidence_id, values, None, attachments)


def test_audit_keeps_accepted_and_removes_rejected_and_orphaned(
    tmp_path: Path,
) -> None:
    (tmp_path / "025主页.jpg").write_bytes(b"img")
    write_decision(_decision(25, True), tmp_path)
    (tmp_path / "026主页.jpg").write_bytes(b"img")
    write_decision(_decision(26, False, "AUTHOR_OVERLAY_BLOCKED"), tmp_path)
    (tmp_path / "047主页.jpg").write_bytes(b"img")  # no decision sidecar
    rows = [
        _row(25, ("025主页.jpg",)),
        _row(26, ("026主页.jpg",)),
        _row(47, ("047主页.jpg",)),
    ]

    updated_rows, entries = audit_staged_author_assets(tmp_path, rows)

    assert (tmp_path / "025主页.jpg").is_file()
    assert not (tmp_path / "026主页.jpg").exists()
    assert not (tmp_path / "047主页.jpg").exists()
    assert {entry["file"] for entry in entries} == {"026主页.jpg", "047主页.jpg"}
    assert entries[0]["action"] == "removed_from_staging"
    assert updated_rows[0].attachment_names == ("025主页.jpg",)
    assert updated_rows[1].attachment_names == ()
    assert updated_rows[2].attachment_names == ()
    assert "附件" not in updated_rows[1].values_by_column
