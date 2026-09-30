from src.auth.window_visibility import reveal_window_once, stage_window_offscreen


def test_stage_window_offscreen_replaces_existing_window_args() -> None:
    options = stage_window_offscreen(
        {
            "headless": False,
            "args": ["--window-position=1,1", "--window-size=320,200", "--mute-audio"],
        },
        width=1440,
        height=900,
    )

    assert options["args"] == [
        "--mute-audio",
        "--window-position=-32000,-32000",
        "--window-size=1440,900",
    ]


class _Session:
    def __init__(self) -> None:
        self.commands: list[tuple[str, object]] = []
        self.detached = False

    async def send(self, name: str, payload: object = None) -> dict[str, int]:
        self.commands.append((name, payload))
        return {"windowId": 7}

    async def detach(self) -> None:
        self.detached = True


class _Context:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def new_cdp_session(self, _page: object) -> _Session:
        return self.session


class _Page:
    def __init__(self, session: _Session) -> None:
        self.context = _Context(session)
        self.front = False

    async def bring_to_front(self) -> None:
        self.front = True


async def test_reveal_window_moves_existing_target_once() -> None:
    session = _Session()
    page = _Page(session)

    assert await reveal_window_once(page, width=1280, height=720) is True
    assert [command[0] for command in session.commands] == [
        "Browser.getWindowForTarget",
        "Browser.setWindowBounds",
        "Browser.setWindowBounds",
    ]
    restore = session.commands[1][1]["bounds"]  # type: ignore[index]
    assert restore == {"windowState": "normal"}
    bounds = session.commands[2][1]["bounds"]  # type: ignore[index]
    assert bounds == {
        "left": 40,
        "top": 40,
        "width": 1280,
        "height": 720,
        "windowState": "normal",
    }
    assert page.front is True
    assert session.detached is True


class _FlakySession:
    def __init__(self, *, fail_first_send: bool) -> None:
        self._fail_first_send = fail_first_send
        self.commands: list[str] = []
        self.detached = False

    async def send(self, name: str, payload: object = None) -> dict[str, int]:
        self.commands.append(name)
        if self._fail_first_send:
            self._fail_first_send = False
            raise RuntimeError("cdp hiccup")
        return {"windowId": 7}

    async def detach(self) -> None:
        self.detached = True


class _FlakyContext:
    def __init__(self, *, failures: int) -> None:
        self.sessions: list[_FlakySession] = []
        self._failures = failures

    async def new_cdp_session(self, _page: object) -> _FlakySession:
        session = _FlakySession(fail_first_send=self._failures > 0)
        self._failures = max(0, self._failures - 1)
        self.sessions.append(session)
        return session


class _SimplePage:
    def __init__(self, context: _FlakyContext) -> None:
        self.context = context
        self.front = False

    async def bring_to_front(self) -> None:
        self.front = True


async def test_reveal_window_retries_once_after_cdp_failure() -> None:
    context = _FlakyContext(failures=1)
    page = _SimplePage(context)

    assert await reveal_window_once(page, width=1280, height=720) is True
    assert [session.commands for session in context.sessions] == [
        ["Browser.getWindowForTarget"],
        [
            "Browser.getWindowForTarget",
            "Browser.setWindowBounds",
            "Browser.setWindowBounds",
        ],
    ]
    assert all(session.detached for session in context.sessions)
    assert page.front is True


async def test_reveal_window_gives_up_after_two_failed_attempts() -> None:
    context = _FlakyContext(failures=2)
    page = _SimplePage(context)

    assert await reveal_window_once(page, width=1280, height=720) is False
    assert len(context.sessions) == 2
    assert all(session.detached for session in context.sessions)
    assert page.front is False
