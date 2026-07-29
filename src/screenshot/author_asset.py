"""Create an optional author-home attachment without duplicating error handling."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.crawler.author_profile_urls import derive_author_profile_url
from src.domain.models import RecordResult, TaskError
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
