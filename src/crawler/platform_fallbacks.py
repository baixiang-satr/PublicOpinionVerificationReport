"""Conservative platform URL fallbacks for known public-page failure modes."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.crawler.platform_types import PlatformDefinition


FALLBACK_TRIGGER_CODES = frozenset(
    {
        "HTTP_403",
        "HTTP_405_ACCESS_RESTRICTED",
        "CONTENT_REDIRECTED_TO_HOME",
        "EMPTY_RENDERED_PAGE",
        "UNEXPECTED_API_RESPONSE",
    }
)


def navigation_candidates(
    original_url: str,
    definition: PlatformDefinition | None,
) -> tuple[str, ...]:
    """Return auditable official-domain URL variants, original URL first."""

    candidates = [original_url]
    if definition is None:
        return tuple(candidates)
    parsed = urlsplit(original_url)
    path = parsed.path

    if definition.key == "hupu":
        host = (parsed.hostname or "").casefold()
        alternate_host = "bbs.hupu.com" if host == "m.hupu.com" else "m.hupu.com"
        candidates.append(urlunsplit((parsed.scheme or "https", alternate_host, path, parsed.query, "")))
    elif definition.key == "tieba":
        match = re.search(r"/p/(\d+)", path)
        if match:
            thread_id = match.group(1)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query["see_lz"] = "1"
            candidates.append(
                urlunsplit(
                    (
                        parsed.scheme or "https",
                        parsed.netloc,
                        path,
                        urlencode(query),
                        "",
                    )
                )
            )
            candidates.append(f"https://tieba.baidu.com/mo/q/m?tid={thread_id}")
    elif definition.key == "dongchedi":
        host = (parsed.hostname or "").casefold()
        alternate_host = (
            "www.dongchedi.com"
            if host.startswith("m.")
            else "m.dongchedi.com"
        )
        candidates.append(
            urlunsplit((parsed.scheme or "https", alternate_host, path, parsed.query, ""))
        )
    elif definition.key == "kuaishou":
        match = re.search(r"/short-video/([^/?#]+)", path)
        if match:
            candidates.append(f"https://m.gifshow.com/fw/photo/{match.group(1)}")

    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def should_try_next_candidate(error_code: str) -> bool:
    return error_code in FALLBACK_TRIGGER_CODES
