"""Cross-site page geometry and horizontal capture alignment."""

from __future__ import annotations

import json
from typing import Any


async def align_page_for_capture(
    page: Any,
    *,
    definition: Any = None,
    focus_selectors: tuple[str, ...] = (),
) -> bool:
    """Bring horizontally displaced substantive content into the viewport.

    Several sites ship off-screen anti-bot/placeholder nodes that expand the
    document to thousands of CSS pixels. Their centered article/profile shell
    is then rendered at the document center, mostly outside the visible
    browser. Automatic screenshots crop to the viewport after this repair;
    interactive OS-level capture needs the live page itself aligned too.

    Existing user horizontal scrolling is respected: the geometry script only
    requests alignment when the page remains at its default left edge.
    """

    dimensions = await page_dimensions(page, definition, focus_selectors)
    if (
        dimensions is None
        or not dimensions["needs_horizontal_alignment"]
        or dimensions["focus_x"] <= 0
        or not hasattr(page, "evaluate")
    ):
        return False
    try:
        await page.evaluate(
            """(left) => {
                const top = window.scrollY || 0;
                window.scrollTo(left, top);
            }""",
            dimensions["focus_x"],
        )
        if hasattr(page, "wait_for_timeout"):
            await page.wait_for_timeout(120)
        return True
    except Exception:  # noqa: BLE001 - layout repair is best-effort
        return False


async def page_dimensions(
    page: Any,
    definition: Any = None,
    focus_selectors: tuple[str, ...] = (),
) -> dict[str, int] | None:
    """Measure document bounds and select a stable horizontal content frame."""

    if not hasattr(page, "evaluate"):
        return None
    selectors = [*focus_selectors]
    selectors.extend(
        [
            selector
            for field in ("content_text", "title")
            for selector in (
                definition.selectors.get(field, ()) if definition else ()
            )
        ]
    )
    # Cross-site defaults are also used by interactive capture, where no
    # router definition is available after the reviewer navigates manually.
    selectors.extend(
        (
            "[class*='profile-header']",
            "[class*='user-info']",
            "[class*='author-info']",
            "[class*='article-content']",
            "[class*='note-content']",
            "article",
            "main h1",
            "main h2",
            "main",
            "[role='main']",
            "h1",
        )
    )
    selector_json = json.dumps(
        list(dict.fromkeys(selectors)),
        ensure_ascii=False,
    )
    try:
        raw = await page.evaluate(
            f"""() => {{
                const selectors = {selector_json};
                const root = document.documentElement;
                const body = document.body;
                const viewportWidth = Math.max(
                    1,
                    window.innerWidth || 0,
                    root?.clientWidth || 0,
                    body?.clientWidth || 0
                );
                const documentWidth = Math.max(
                    viewportWidth,
                    root?.scrollWidth || 0,
                    root?.offsetWidth || 0,
                    body?.scrollWidth || 0,
                    body?.offsetWidth || 0
                );
                const height = Math.max(
                    1,
                    root?.scrollHeight || 0,
                    root?.offsetHeight || 0,
                    body?.scrollHeight || 0,
                    body?.offsetHeight || 0
                );
                let focusX = scrollX || 0;
                let needsHorizontalAlignment = false;
                for (const selector of selectors) {{
                  let elements = [];
                  try {{ elements = Array.from(document.querySelectorAll(selector)); }}
                  catch (_) {{ continue; }}
                  const candidate = elements.find(element => {{
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    const text = (element.innerText || element.textContent || '').trim();
                    const mediaSurface = element.matches?.('video')
                      || Boolean(element.querySelector?.('video'));
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && rect.width >= 120
                      && rect.height >= 20
                      && (
                        text.length >= 2
                        || (mediaSurface && rect.width >= 240 && rect.height >= 180)
                      );
                  }});
                  if (candidate) {{
                    const rect = candidate.getBoundingClientRect();
                    const visibleWidth = Math.max(
                      0,
                      Math.min(viewportWidth, rect.right) - Math.max(0, rect.left)
                    );
                    const relevantWidth = Math.max(
                      1,
                      Math.min(viewportWidth, rect.width)
                    );
                    const visibleRatio = visibleWidth / relevantWidth;
                    const wellFramed = visibleRatio >= 0.75
                      && rect.left >= -32
                      && rect.left <= viewportWidth * 0.45;
                    if (!wellFramed) {{
                      const leftGutter = Math.min(
                        160,
                        Math.max(48, viewportWidth * 0.10)
                      );
                      focusX = Math.max(
                        0,
                        Math.min(
                          documentWidth - viewportWidth,
                          rect.left + scrollX - leftGutter
                        )
                      );
                      // Only repair the site's default layout. A reviewer who
                      // intentionally scrolled a wide table stays in control.
                      needsHorizontalAlignment = (scrollX || 0) <= 4
                        && Math.abs(focusX - (scrollX || 0)) > 8;
                    }}
                    break;
                  }}
                }}
                return {{
                  viewportWidth,
                  documentWidth,
                  height,
                  focusX,
                  scrollX: scrollX || 0,
                  needsHorizontalAlignment
                }};
            }}"""
        )
        viewport_width = min(32_767, max(1, int(raw["viewportWidth"])))
        document_width = min(
            32_767,
            max(viewport_width, int(raw["documentWidth"])),
        )
        height = max(1, int(raw["height"]))
        max_focus_x = max(0, document_width - viewport_width)
        focus_x = min(max_focus_x, max(0, int(raw.get("focusX") or 0)))
        scroll_x = min(max_focus_x, max(0, int(raw.get("scrollX") or 0)))
        return {
            "viewport_width": viewport_width,
            "document_width": document_width,
            "height": height,
            "focus_x": focus_x,
            "scroll_x": scroll_x,
            "needs_horizontal_alignment": bool(
                raw.get("needsHorizontalAlignment", focus_x != scroll_x)
            ),
        }
    except Exception:  # noqa: BLE001 - geometry probing is best-effort
        return None
