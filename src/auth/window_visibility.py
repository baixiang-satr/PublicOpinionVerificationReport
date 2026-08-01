"""Prepare interactive Chromium windows off-screen and reveal them once."""

from __future__ import annotations

from typing import Any


def stage_window_offscreen(
    launch_options: dict[str, Any],
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Return launch options that keep the initial ``about:blank`` hidden."""

    options = dict(launch_options)
    args = [
        item
        for item in options.get("args", ())
        if not str(item).startswith(("--window-position=", "--window-size="))
    ]
    args.extend(
        (
            "--window-position=-32000,-32000",
            f"--window-size={max(800, width)},{max(600, height)}",
        )
    )
    options["args"] = args
    return options


async def reveal_window_once(
    page: Any,
    *,
    width: int,
    height: int,
) -> bool:
    """Move the already-rendered page on-screen without reopening a browser."""

    context = getattr(page, "context", None)
    if context is None or not hasattr(context, "new_cdp_session"):
        return False
    session = None
    try:
        session = await context.new_cdp_session(page)
        window = await session.send("Browser.getWindowForTarget")
        window_id = int(window["windowId"])
        await session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": 40,
                    "top": 40,
                    "width": max(800, width),
                    "height": max(600, height),
                    "windowState": "normal",
                },
            },
        )
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return True
    except Exception:
        return False
    finally:
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass
