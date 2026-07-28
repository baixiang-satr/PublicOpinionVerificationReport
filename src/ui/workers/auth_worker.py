"""Background worker for guest probing and visible user-authorized login."""

from __future__ import annotations

import asyncio
from threading import Event

from PyQt5.QtCore import QThread, pyqtSignal

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.service import AuthManagerService
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig


class AuthWorker(QThread):
    progress = pyqtSignal(str, str, str)
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        action: str,
        config: TaskConfig,
        store: AuthProfileStore,
        *,
        platform_key: str | None = None,
        phone: str | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._action = action
        self._config = config
        self._store = store
        self._platform_key = platform_key
        self._phone = phone
        self._cancel_event = Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except asyncio.CancelledError:
            return
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")

    async def _run_async(self) -> None:
        service = AuthManagerService(
            self._config,
            self._store,
            legacy_state_path=self._config.storage_state_path,
        )
        callback = self._emit_progress
        if self._action == "probe_all":
            results = await service.probe_all_guest(
                cancel_event=self._cancel_event,
                on_progress=callback,
            )
            for result in results:
                self.result_ready.emit(result)
            return
        if self._platform_key is None:
            raise ValueError("A platform must be selected for this authentication action.")
        result = await service.probe(
            self._platform_key,
            use_saved_state=self._action != "probe_guest",
            interactive=self._action == "login",
            phone=self._phone if self._action == "login" else None,
            cancel_event=self._cancel_event,
            on_progress=callback,
        )
        self.result_ready.emit(result)

    def _emit_progress(
        self,
        platform_key: str,
        status: AuthStatus,
        message: str,
    ) -> None:
        self.progress.emit(platform_key, status.value, message)
