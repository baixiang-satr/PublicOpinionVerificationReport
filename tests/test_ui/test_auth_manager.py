from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication

from src.auth.registry import AUTH_POLICIES
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.ui.app import create_application
from src.ui.widgets.auth_manager import AuthManagerDialog


pytestmark = pytest.mark.ui


class ReverseProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


def test_auth_manager_lists_all_platforms_and_keeps_phone_optional(
    app: QApplication,
    tmp_path: Path,
) -> None:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    dialog = AuthManagerDialog(
        TaskConfig(auth_store_dir=tmp_path / "auth"),
        store,
    )
    dialog.show()
    app.processEvents()

    assert dialog.table.rowCount() == 34
    assert dialog.table.rowCount() == len(AUTH_POLICIES)
    assert dialog.probe_all_button.text() == "验证全部（游客优先）"
    assert dialog.login_button.text() == "登录 / 更新选中"
    assert "不会自动发送验证码" in dialog.phone_input.placeholderText()
    assert dialog.phone_input.text() == ""

    dialog.close()
    app.processEvents()
