"""Filter a legacy combined Playwright state to one platform's domains."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from src.auth.registry import auth_policy_for_key


def filter_state_for_policy(
    state: dict[str, Any],
    platform_key: str,
) -> dict[str, Any]:
    policy = auth_policy_for_key(platform_key)

    def allowed_host(host: str) -> bool:
        normalized = host.lstrip(".").casefold()
        return any(
            normalized == suffix or normalized.endswith(f".{suffix}")
            for suffix in policy.host_suffixes
        )

    cookies = [
        cookie
        for cookie in state.get("cookies", [])
        if isinstance(cookie, dict) and allowed_host(str(cookie.get("domain", "")))
    ]
    origins = []
    for origin in state.get("origins", []):
        if not isinstance(origin, dict):
            continue
        host = urlsplit(str(origin.get("origin", ""))).hostname or ""
        if allowed_host(host):
            origins.append(origin)
    return {"cookies": cookies, "origins": origins}
