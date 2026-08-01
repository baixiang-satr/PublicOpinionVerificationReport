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
    ]
    bounds = session.commands[1][1]["bounds"]  # type: ignore[index]
    assert bounds == {
        "left": 40,
        "top": 40,
        "width": 1280,
        "height": 720,
        "windowState": "normal",
    }
    assert page.front is True
    assert session.detached is True
