"""WebUIBridge 测试：全部离线（假窗口、tmp 目录、无浏览器/外网/Cookie）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.auth.registry import AUTH_POLICIES
from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.domain.models import (
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.license.models import LicenseInfo, LicenseStatus
from src.services.checkpoint_store import CheckpointStore
import src.webui.bridge as bridge_module
from src.webui.bridge import WebUIBridge
from src.webui.runner import EventSink


class _AlwaysValidLicense:
    """测试替身：永远视为已激活（本模块不测试许可证逻辑本身）。"""

    def status(self) -> LicenseInfo:
        return LicenseInfo(
            activated=True,
            status=LicenseStatus.VALID,
            message="",
            machine_code="TEST",
        )


@pytest.fixture(autouse=True)
def _always_licensed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bridge_module, "default_license_manager", _AlwaysValidLicense)


def _record(eid: int, url: str, sheet: str, status: RecordStatus) -> RecordResult:
    return RecordResult(
        task=UrlTask(eid, url, url),
        status=status,
        route=RouteDecision(sheet_name=sheet, platform_value="", text_type="正文"),
    )


def _config(tmp_path: Path) -> AppConfig:
    template = TemplateConfig(output_dir=tmp_path / "output")
    task = TaskConfig(auth_store_dir=tmp_path / "auth")
    return AppConfig(template=template, task=task)


def _make_job(tmp_path: Path, records: list[RecordResult]) -> Path:
    job_dir = tmp_path / "output" / "job-test"
    job_dir.mkdir(parents=True)
    tasks = tuple(record.task for record in records)
    store = CheckpointStore(job_dir / "job_checkpoint.json", job_id="job-test", tasks=tasks)
    store.update_many(records)
    store.save()
    return job_dir


class _FakeWindow:
    def __init__(self, picked: Path | None) -> None:
        self._picked = picked
        self.calls: list[tuple[tuple, dict]] = []

    def create_file_dialog(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return (str(self._picked),) if self._picked else None


def test_bootstrap_and_options(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    boot = bridge.get_bootstrap()
    assert boot["session"] is None
    assert boot["options"]["max_concurrency"] >= 1
    json.dumps(boot)

    assert bridge.set_options(
        {
            "max_concurrency": 5,
            "page_timeout_seconds": 60,
            "max_retries": 2,
            "screenshot_format": "png",
            "headless": False,
        }
    ) == {"ok": True}
    boot = bridge.get_bootstrap()
    assert boot["options"]["max_concurrency"] == 5
    assert boot["options"]["screenshot_format"] == "png"
    assert boot["options"]["headless"] is False

    bad = bridge.set_options({"max_concurrency": "abc"})
    assert bad["ok"] is False


def test_edits_require_session(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    assert bridge.apply_edit(1, "author_name", "x")["ok"] is False
    assert bridge.get_sheet_payload() == []
    assert bridge.export_zip()["ok"] is False
    assert bridge.resume_checkpoint(True)["ok"] is False


def test_open_session_and_review_mutations(tmp_path: Path) -> None:
    records = [
        _record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW),
        _record(2, "https://example.com/b", "微博博客", RecordStatus.EXPORTED),
    ]
    job_dir = _make_job(tmp_path, records)
    bridge = WebUIBridge(_config(tmp_path), EventSink())

    ok, message = bridge.jobs.open_session(job_dir)
    assert ok, message
    payload = bridge.get_sheet_payload()
    weibo = next(sheet for sheet in payload if sheet["name"] == "微博博客")
    assert len(weibo["rows"]) == 2

    result = bridge.apply_edit(1, "author_name", "小明")
    assert result["ok"] is True
    assert "昵称" not in result["row"]["missing"]

    added = bridge.add_manual_row("群聊")
    assert added["eid"] is not None
    assert bridge.remove_manual_row(added["eid"])["ok"] is True


def test_open_session_rejects_bad_dir(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    ok, message = bridge.jobs.open_session(tmp_path / "missing")
    assert not ok
    assert message


def test_pick_screenshot_stages_manual_asset(tmp_path: Path) -> None:
    records = [_record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW)]
    job_dir = _make_job(tmp_path, records)
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    bridge = WebUIBridge(
        _config(tmp_path),
        EventSink(),
        window_provider=lambda: _FakeWindow(image),
    )
    ok, _ = bridge.jobs.open_session(job_dir)
    assert ok

    result = bridge.pick_screenshot(1, "primary")
    assert result["ok"] is True
    assert result["name"].startswith("001_")
    assert (job_dir / "manual_assets" / result["name"]).is_file()

    payload = bridge.list_screenshots(1)
    assert payload["content"] is not None
    assert payload["content"]["data_url"].startswith("data:image/png;base64,")
    assert payload["content"]["name"] == result["name"]
    assert payload["author"] is None

    author = bridge.pick_screenshot(1, "author")
    assert author["ok"] is True
    both = bridge.list_screenshots(1)
    assert both["author"] is not None
    assert both["author"]["name"] == author["name"]

    again = bridge.pick_screenshot(1, "attachment")
    assert again["ok"] is True
    # 附件槽位不影响个人页截图槽位
    still = bridge.list_screenshots(1)
    assert still["author"] is not None


def test_list_screenshots_empty_without_session_or_files(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    assert bridge.list_screenshots(1) == {"content": None, "author": None}

    records = [_record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW)]
    job_dir = _make_job(tmp_path, records)
    bridge.jobs.open_session(job_dir)
    assert bridge.list_screenshots(1) == {"content": None, "author": None}
    assert bridge.list_screenshots(999) == {"content": None, "author": None}


def test_crawled_author_screenshot_is_visible_and_listed_in_primary_column(
    tmp_path: Path,
) -> None:
    record = _paged_record(
        1,
        "https://v.douyin.com/erOICsACek8/",
        final_url="https://www.douyin.com/video/7667987870472788815",
        author_url="https://www.douyin.com/user/SEC123",
    )
    record.assets.page_screenshot = tmp_path / "001.jpg"
    record.assets.author_screenshot = tmp_path / "001主页.jpg"
    record.assets.page_screenshot.write_bytes(b"\xff\xd8\xff" + b"0" * 32)
    record.assets.author_screenshot.write_bytes(b"\xff\xd8\xff" + b"1" * 32)
    job_dir = _make_job(tmp_path, [record])
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    ok, message = bridge.jobs.open_session(job_dir)
    assert ok, message

    previews = bridge.list_screenshots(1)
    assert previews["content"] is not None
    assert previews["author"] is not None
    assert previews["content"]["name"] == "001.jpg"
    assert previews["author"]["name"] == "001主页.jpg"

    payload = bridge.get_sheet_payload()
    video = next(sheet for sheet in payload if sheet["name"] == "图文视频")
    # 对调表：账号截图列(H)交付个人主页截图，内容页截图进其他文件名(I)
    assert video["rows"][0]["cells"]["H"] == "001主页.jpg"
    assert video["rows"][0]["cells"]["I"] == "001.jpg"


def test_start_region_capture_guards(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    no_session = bridge.start_region_capture(1, "content")
    assert no_session["ok"] is False
    assert no_session["code"] == "no_session"

    records = [
        _record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW),
    ]
    job_dir = _make_job(tmp_path, records)
    bridge.jobs.open_session(job_dir)

    bad_target = bridge.start_region_capture(1, "wat")
    assert bad_target["ok"] is False
    assert bad_target["code"] == "bad_target"

    missing = bridge.start_region_capture(999, "content")
    assert missing["ok"] is False
    assert missing["code"] == "no_record"

    added = bridge.add_manual_row("群聊")
    assert added["eid"] is not None
    no_url = bridge.start_region_capture(added["eid"], "content")
    assert no_url["ok"] is False
    assert no_url["code"] == "no_url"


class _FakeCapture:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start(self, **kwargs):
        self.calls.append(kwargs)
        return True, ""


def _paged_record(
    eid: int,
    url: str,
    *,
    final_url: str | None,
    author_url: str | None,
) -> RecordResult:
    return RecordResult(
        task=UrlTask(eid, url, url),
        status=RecordStatus.NEEDS_REVIEW,
        route=RouteDecision(sheet_name="图文视频", platform_value="", text_type="正文"),
        page=PageData(final_url=final_url, author_url=author_url),
    )


def test_start_region_capture_author_target_opens_profile_url_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截取个人页有 author_url 时直达，不再要求窗口内手动跳转。"""

    records = [
        _paged_record(
            1,
            "https://v.douyin.com/erOICsACek8/",
            final_url="https://www.douyin.com/video/7557",
            author_url="https://www.douyin.com/user/secABC123",
        ),
        _paged_record(
            2,
            "https://v.douyin.com/6OYduQ_wgKk/",
            final_url="https://www.douyin.com/video/7558",
            author_url=None,
        ),
    ]
    records[0].page.author_name = "目标主页作者"
    records[0].page.author_id = "target-7788"
    job_dir = _make_job(tmp_path, records)
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    ok, _message = bridge.jobs.open_session(job_dir)
    assert ok
    capture = _FakeCapture()
    monkeypatch.setattr(bridge, "capture", capture)
    monkeypatch.setattr(
        bridge,
        "_capture_storage_state",
        lambda _url: {"cookies": [{"name": "sessionid", "value": "x"}]},
    )

    result = bridge.start_region_capture(1, "author")
    assert result["ok"] is True
    assert capture.calls[-1]["url"] == "https://www.douyin.com/user/secABC123"
    assert capture.calls[-1]["platform_key"] == "douyin"
    assert capture.calls[-1]["focus_texts"] == (
        "目标主页作者",
        "target-7788",
    )

    # 无 author_url 时回落到内容页
    result = bridge.start_region_capture(2, "author")
    assert result["ok"] is True
    assert capture.calls[-1]["url"] == "https://www.douyin.com/video/7558"

    # 截取内容页始终用内容 URL
    result = bridge.start_region_capture(1, "content")
    assert result["ok"] is True
    assert capture.calls[-1]["url"] == "https://www.douyin.com/video/7557"


def test_pick_screenshot_cancelled(tmp_path: Path) -> None:
    records = [_record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW)]
    job_dir = _make_job(tmp_path, records)
    bridge = WebUIBridge(
        _config(tmp_path),
        EventSink(),
        window_provider=lambda: _FakeWindow(None),
    )
    bridge.jobs.open_session(job_dir)
    assert bridge.pick_screenshot(1, "primary") == {"ok": False, "name": ""}


def test_auth_list_and_logout(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    platforms = bridge.auth_list()
    assert len(platforms) == len(AUTH_POLICIES)
    assert all(p["status"] == "unknown" for p in platforms)
    json.dumps(platforms)
    assert bridge.auth_logout(platforms[0]["key"]) == {"ok": True}


def test_auth_start_guard(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())

    class _Running:
        def is_running(self) -> bool:
            return True

    runner = bridge.auth
    runner.is_running = _Running().is_running  # type: ignore[method-assign]
    assert runner.start("probe_all")[0] is False


def test_auth_login_all_is_disabled_to_prevent_window_churn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    calls: list[tuple[str, str | None]] = []

    def fake_start(action: str, platform_key: str | None = None):
        calls.append((action, platform_key))
        return True, ""

    monkeypatch.setattr(bridge.auth, "start", fake_start)

    assert bridge.auth_login_all() == {
        "ok": False,
        "message": "批量弹出登录页已停用，请逐个平台点击“登录 / 更新”。",
    }
    assert calls == []


def test_auth_probe_relevant_delegates_to_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    calls: list[tuple[str, str | None]] = []

    def fake_start(action: str, platform_key: str | None = None):
        calls.append((action, platform_key))
        return True, ""

    monkeypatch.setattr(bridge.auth, "start", fake_start)

    assert bridge.auth_probe_relevant() == {"ok": True, "message": ""}
    assert calls == [("probe_relevant", None)]


def test_auth_resume_login_requires_running_job(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    result = bridge.auth_resume_login("weibo", "skip")
    assert result["ok"] is False


def test_auth_resume_login_forwards_decision(tmp_path: Path) -> None:
    bridge = WebUIBridge(_config(tmp_path), EventSink())
    decisions: list[tuple[str, str]] = []

    class _Coordinator:
        def resume(self, platform_key: str, action: str):
            decisions.append((platform_key, action))
            return True, ""

    bridge.jobs.relogin = _Coordinator()  # type: ignore[assignment]
    bridge.jobs.is_running = lambda: True  # type: ignore[method-assign]
    assert bridge.auth_resume_login("weibo", "retry") == {"ok": True, "message": ""}
    assert decisions == [("weibo", "retry")]


def test_event_sink_without_window_is_silent() -> None:
    sink = EventSink()
    sink.emit("progress", {"percent": 1})  # 不抛异常即可


@pytest.mark.parametrize("suffix", [".txt"])
def test_pick_input_file(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"urls{suffix}"
    source.write_text("https://example.com/a\nhttps://example.com/b\nnot-a-url\n", encoding="utf-8")
    window = _FakeWindow(source)
    bridge = WebUIBridge(
        _config(tmp_path),
        EventSink(),
        window_provider=lambda: window,
    )
    info = bridge.pick_input_file()
    assert info is not None
    assert info["url_count"] == 2
    assert info["rejected_count"] == 1
    assert window.calls
    import webview

    args, kwargs = window.calls[0]
    assert args == (webview.FileDialog.OPEN,)
    assert kwargs["file_types"][0].startswith("URL 文件")
