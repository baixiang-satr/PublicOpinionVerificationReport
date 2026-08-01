"""Authentication-list presentation and pre-crawl gate helpers."""

from __future__ import annotations

from typing import Any

from src.auth.login_evidence import state_has_authenticated_session
from src.auth.registry import AUTH_POLICIES, auth_policy_for_url
from src.config.settings import TaskConfig
from src.webui.serialize import auth_profile_payload


def build_auth_list(store: Any, input_platform_keys: set[str]) -> list[dict]:
    payloads: list[dict] = []
    for order, policy in enumerate(AUTH_POLICIES):
        profile = store.profile_for(policy.platform_key)
        payload = auth_profile_payload(policy.display_name, profile)
        try:
            state = store.load_state(policy.platform_key, include_inactive=True)
        except Exception:  # noqa: BLE001 - unreadable state is shown as requiring login
            state = None
        if (
            state is not None
            and state_has_authenticated_session(policy.platform_key, state) is False
        ):
            payload.update(
                status="auth_required",
                status_text="需要重新登录",
                tone="warn",
                message=(
                    "旧状态只有游客/设备 Cookie，没有检测到账号登录凭据；"
                    "请执行“登录 / 更新”。"
                ),
            )
        payload["relevant"] = policy.platform_key in input_platform_keys
        payload["catalog_order"] = order
        payloads.append(payload)
    payloads.sort(
        key=lambda item: (
            not bool(item["relevant"]),
            int(item["catalog_order"]),
        )
    )
    for payload in payloads:
        payload.pop("catalog_order", None)
    return payloads


def missing_auth_platforms(config: TaskConfig, store: Any, tasks: Any) -> list[str]:
    """Return task platforms lacking a usable account-scoped state."""

    if not config.enable_auth_health_gate:
        return []
    missing: list[str] = []
    seen: set[str] = set()
    for task in tasks:
        policy = auth_policy_for_url(task.normalized_url)
        if policy is None or policy.platform_key in seen:
            continue
        seen.add(policy.platform_key)
        try:
            # Preserved inactive state is still reusable: the crawl browser
            # performs a hidden fresh-context revalidation before visiting
            # content.  Do not force another manual login merely because an
            # old probe marked the profile stale.
            state = store.load_state(
                policy.platform_key,
                include_inactive=True,
            )
        except Exception:  # noqa: BLE001 - unreadable states must block crawl
            state = None
        if state is None or state_has_authenticated_session(
            policy.platform_key,
            state,
        ) is False:
            missing.append(policy.display_name)
    return missing
