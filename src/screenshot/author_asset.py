"""Create an optional author-home attachment without duplicating error handling."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.crawler.author_profile_urls import derive_author_profile_url
from src.domain.models import ExtractionSource, RecordResult, TaskError
from src.screenshot.author_evidence import (
    AuthorEvidenceDecision,
    ProfilePageType,
    write_decision,
)
from src.screenshot.author_shooter import AuthorScreenshotError


async def capture_author_home_asset(
    shooter: Any,
    source_page: Any,
    result: RecordResult,
    output_dir: Path,
    cancel_event: asyncio.Event,
) -> tuple[Path | None, TaskError | None]:
    """Capture a home page, or reuse the primary evidence for a direct profile URL."""

    author_url = result.page.author_url
    if not author_url:
        return None, None
    if _is_self_profile(author_url):
        # 查看者自己的主页（抖音登录后导航栏 /user/self）绝不是作者证据。
        return None, TaskError(
            "author_screenshot",
            "AUTHOR_URL_INVALID",
            "候选主页是查看者自己的主页（/user/self），不能作为作者证据。",
            retryable=False,
        )
    primary = result.assets.page_screenshot
    if (
        primary is not None
        and _same_document(author_url, result.page.final_url)
        and _looks_like_profile_url(author_url)
    ):
        try:
            suffix = primary.suffix.casefold()
            destination = Path(output_dir).resolve() / (
                f"{result.task.evidence_id:03d}主页{suffix}"
            )
            await asyncio.to_thread(shutil.copy2, primary, destination)
            decision = AuthorEvidenceDecision(
                candidate_url=author_url,
                evidence_id=result.task.evidence_id,
                candidate_source="same_document",
                expected_name=result.page.author_name,
                expected_id=(
                    None
                    if result.page.author_id_is_fallback
                    else result.page.author_id
                ),
                detected_name=result.page.author_name,
                detected_id=(
                    None
                    if result.page.author_id_is_fallback
                    else result.page.author_id
                ),
                page_type=ProfilePageType.PERSON_PROFILE.value,
                access_state="accessible",
                overlay_state="clear",
                capture_region="profile_container",
                identity_state="verified",
                accepted=True,
            )
            await asyncio.to_thread(write_decision, decision, Path(output_dir))
            return destination, None
        except Exception as error:
            return None, TaskError(
                "author_screenshot",
                "AUTHOR_SCREENSHOT_FAILED",
                f"复制直接个人主页截图失败：{error}",
                retryable=False,
            )
    try:
        verified: dict[str, str] = {}

        def _decision_sink(decision: AuthorEvidenceDecision) -> None:
            if (
                decision.accepted
                and decision.identity_state == "verified"
                and decision.detected_id
                and decision.detected_id_source in {"text", "douyin"}
            ):
                # 只接受页面上直接显示的账号（抖音号/UID 文本）；href 里的
                # sec_uid 只是链接标识，不是用户可见账号。
                verified["author_id"] = decision.detected_id
                verified["source"] = decision.detected_id_source

        path = await shooter.capture(
            source_page,
            author_url,
            result.task.evidence_id,
            output_dir,
            cancel_event,
            expected_author_name=result.page.author_name,
            expected_author_id=(
                None
                if result.page.author_id_is_fallback
                else result.page.author_id
            ),
            candidate_source=_candidate_source(author_url, result.page.final_url),
            decision_sink=_decision_sink,
        )
        _backfill_author_id(
            result,
            verified.get("author_id"),
            upgrade=verified.get("source") == "douyin",
        )
        return path, None
    except AuthorScreenshotError as error:
        return None, TaskError(
            "author_screenshot",
            error.code,
            str(error),
            retryable=False,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        return None, TaskError(
            "author_screenshot",
            "AUTHOR_SCREENSHOT_FAILED",
            str(error),
            retryable=False,
        )


def _backfill_author_id(
    result: RecordResult,
    detected_id: str | None,
    *,
    upgrade: bool = False,
) -> None:
    """Prefer the account id shown on the identity-verified author home page.

    Douyin's aweme JSON often omits ``unique_id``; the profile header shows
    「抖音号」 directly.  Only a *verified* decision may overwrite the
    nickname fallback, never an unverified probe.  With ``upgrade=True``
    (the id was read from an explicit 抖音号 label) even a JSON-sourced
    internal uid is replaced: the profile-displayed account is what the
    template's 账号 column means.
    """

    if not detected_id:
        return
    page = result.page
    if page.author_id and not page.author_id_is_fallback and not upgrade:
        return
    page.author_id = detected_id
    page.author_id_is_fallback = False
    page.field_sources["author_id"] = ExtractionSource.PLATFORM_DOM
    page.field_confidences["author_id"] = 0.95 if upgrade else 0.9


_SELF_PROFILE_RE = re.compile(r"/user/self(?:[/?#]|$)", re.I)


def _is_self_profile(url: str) -> bool:
    return bool(_SELF_PROFILE_RE.search(urlsplit(url).path))


def _same_document(left: str, right: str | None) -> bool:
    if not right:
        return False

    def normalized(value: str) -> str:
        parts = urlsplit(value)
        path = parts.path.rstrip("/") or "/"
        return urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                path,
                parts.query,
                "",
            )
        )

    return normalized(left) == normalized(right)


def _looks_like_profile_url(url: str) -> bool:
    path = urlsplit(url).path
    return bool(
        re.search(
            r"/(?:profile|user|space|author|people|member|account|home/main)(?:/|$)",
            path,
            re.I,
        )
    )


def _candidate_source(author_url: str, final_url: str | None) -> str:
    """Label where the candidate came from; generic JSON-LD org links excluded."""

    if derive_author_profile_url(final_url or author_url, None) == author_url:
        return "platform_derived"
    if _looks_like_profile_url(author_url):
        return "author_link"
    return "dom_or_derived"
