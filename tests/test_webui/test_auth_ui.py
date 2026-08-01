from pathlib import Path

from src.auth.models import AuthProfile, AuthStatus
from src.auth.registry import auth_policy_for_key
from src.config.settings import TaskConfig
from src.domain.models import UrlTask
from src.webui.auth_ui import build_auth_list, missing_auth_platforms


class _Store:
    def __init__(self, states: dict[str, dict]) -> None:
        self.states = states

    def profile_for(self, platform_key: str) -> AuthProfile:
        policy = auth_policy_for_key(platform_key)
        return AuthProfile(
            profile_id=f"{platform_key}-primary",
            platform_key=platform_key,
            auth_scope=policy.auth_scope,
            status=AuthStatus.VALID,
            state_filename=f"{platform_key}.dpapi",
        )

    def load_state(self, platform_key: str, *, include_inactive: bool = False):
        del include_inactive
        return self.states.get(platform_key)


def test_auth_list_does_not_present_guest_wechat_video_state_as_valid() -> None:
    store = _Store({"wechat_video": {"cookies": [], "origins": []}})

    platforms = build_auth_list(store, {"wechat_video"})
    video = next(item for item in platforms if item["key"] == "wechat_video")

    assert video["status"] == "auth_required"
    assert video["status_text"] == "需要重新登录"
    assert video["relevant"] is True
    assert "游客" in video["message"]


def test_missing_auth_platforms_blocks_guest_wechat_video_before_navigation() -> None:
    store = _Store({"wechat_video": {"cookies": [], "origins": []}})
    task = UrlTask(
        1,
        "https://weixin.qq.com/sph/AiQbKWmgTm",
        "https://weixin.qq.com/sph/AiQbKWmgTm",
    )
    config = TaskConfig(
        auth_store_dir=Path("auth"),
        enable_auth_health_gate=True,
    )

    missing = missing_auth_platforms(config, store, [task])

    assert missing == [auth_policy_for_key("wechat_video").display_name]
