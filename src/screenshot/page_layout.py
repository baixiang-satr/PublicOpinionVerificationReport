"""Cross-site page geometry and horizontal capture alignment."""

from __future__ import annotations

import json
from typing import Any


async def align_page_for_capture(
    page: Any,
    *,
    definition: Any = None,
    focus_selectors: tuple[str, ...] = (),
    focus_texts: tuple[str, ...] = (),
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

    await _reset_browser_zoom(page)
    dimensions = await page_dimensions(
        page,
        definition,
        focus_selectors,
        focus_texts,
    )
    if dimensions is None:
        return False
    if (focus_selectors or focus_texts) and not dimensions["focus_found"]:
        return False
    # A wide document can be caused by an irrelevant off-screen placeholder
    # while the selected evidence surface is already fully visible.  Callers
    # ask this helper whenever overflow exists, so "nothing to move" is a
    # successful alignment result rather than a capture failure.
    if not dimensions["needs_horizontal_alignment"]:
        return True
    if not hasattr(page, "evaluate"):
        return False
    try:
        original_scroll_x = dimensions["scroll_x"]
        if dimensions["focus_x"] > 0:
            await page.evaluate(
                """(left) => {
                    const top = window.scrollY || 0;
                    window.scrollTo(left, top);
                }""",
                dimensions["focus_x"],
            )
        else:
            # Never rewrite the target site's transforms.  Doing so can move
            # only one SPA subtree while headers/overlays keep their original
            # coordinate system, producing the characteristic left-cropped
            # evidence image.  Native scrollIntoView is reversible and keeps
            # the browser and OS-level manual screenshot in the same frame.
            await page.evaluate(
                """(target) => {
                    let elements = [];
                    try { elements = Array.from(document.querySelectorAll(target.selector)); }
                    catch (_) { return false; }
                    const element = elements[target.index] || elements[0];
                    if (!element) return false;
                    element.scrollIntoView({block: 'nearest', inline: 'center'});
                    return true;
                }""",
                {
                    "selector": dimensions.get("focus_selector", "main"),
                    "index": dimensions.get("focus_index", 0),
                },
            )
        if hasattr(page, "wait_for_timeout"):
            await page.wait_for_timeout(120)
        repaired = await page_dimensions(
            page,
            definition,
            focus_selectors,
            focus_texts,
        )
        if repaired is not None:
            if (focus_selectors or focus_texts) and not repaired["focus_found"]:
                return False
            if not repaired["focus_geometry_measured"]:
                return True
            minimum_left = (
                -16
                if repaired["focus_x"] <= 4
                else max(48, repaired["desired_left"] - 16)
            )
            well_framed = (
                repaired["focus_visible_ratio"] >= 0.75
                and repaired["focus_left"] >= minimum_left
                and repaired["focus_left"]
                <= repaired["viewport_width"] * 0.45
            )
            if well_framed:
                return True
        # A failed best-effort move must not leave an operator's page in a
        # different place.  The caller can then reject an unframed automatic
        # screenshot instead of silently shipping partial evidence.
        await page.evaluate(
            """(left) => window.scrollTo(left, window.scrollY || 0)""",
            original_scroll_x,
        )
        return False
    except Exception:  # noqa: BLE001 - layout repair is best-effort
        return False


async def page_dimensions(
    page: Any,
    definition: Any = None,
    focus_selectors: tuple[str, ...] = (),
    focus_texts: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Measure document bounds and select a stable horizontal content frame."""

    if not hasattr(page, "evaluate"):
        return None
    selectors = [*focus_selectors]
    if not focus_selectors:
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
    if not focus_selectors:
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
    focus_text_json = json.dumps(
        [text.strip() for text in focus_texts if text and text.strip()],
        ensure_ascii=False,
    )
    try:
        raw = await page.evaluate(
            f"""() => {{
                const selectors = {selector_json};
                const wantedTexts = {focus_text_json};
                const root = document.documentElement;
                const body = document.body;
                const viewportWidth = Math.max(
                    1,
                    window.visualViewport?.width
                      || window.innerWidth
                      || root?.clientWidth
                      || 0
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
                let focusSelector = '';
                let focusIndex = 0;
                let focusFound = false;
                const leftGutter = Math.min(
                  280,
                  Math.max(96, viewportWidth * 0.16)
                );
                let desiredLeft = leftGutter;
                let focusVisibleRatio = 1;
                let focusLeft = 0;
                document.querySelectorAll('[data-por-capture-focus]').forEach(
                  element => element.removeAttribute('data-por-capture-focus')
                );
                // Profile shells often have a generic 4000px-wide container.
                // Anchor on the compact account header carrying a public ID,
                // rather than the first unrelated navigation "profile" node.
                const normalize = value => (value || '')
                  .toLocaleLowerCase()
                  .replace(/[\\s·•_\\-—:：|]+/g, '');
                const wantedKeys = wantedTexts.map(normalize).filter(Boolean);
                const identityMarker = wantedKeys.length
                  ? Array.from(document.querySelectorAll('body *')).find(element => {{
                      if (element.children.length > 4) return false;
                      const text = (element.innerText || element.textContent || '').trim();
                      if (!text || text.length > 300) return false;
                      const key = normalize(text);
                      return wantedKeys.some(wanted =>
                        key.includes(wanted) || wanted.includes(key)
                      );
                    }})
                  : null;
                const accountMarker = identityMarker || Array.from(document.querySelectorAll('body *'))
                  .find(element => {{
                    if (element.children.length > 2) return false;
                    const text = (element.innerText || element.textContent || '').trim();
                    if (!text || text.length > 240) return false;
                    return /(?:抖音号|快手号|头条号|小红书号|账号|UID)[:：]/i.test(text);
                  }});
                if (accountMarker) {{
                  const marker = accountMarker;
                  let container = marker;
                  while (container && container !== body) {{
                    const rect = container.getBoundingClientRect();
                    if (
                      rect.width >= 300
                      && rect.width <= viewportWidth * 1.5
                      && rect.height >= 72
                      && rect.height <= 600
                    ) break;
                    container = container.parentElement;
                  }}
                  if (!container || container === body || container === root) {{
                    // Minimal but valid profile pages may contain only an h1.
                    // Keep the identity-bearing node instead of falling back
                    // to an unrelated navigation element.
                    container = marker;
                  }}
                  const markerRect = container?.getBoundingClientRect?.();
                  if (
                    container
                    && markerRect
                    && markerRect.width >= 80
                    && markerRect.height >= 18
                  ) {{
                    container.setAttribute('data-por-capture-focus', '1');
                    selectors.unshift('[data-por-capture-focus]');
                  }}
                }}
                for (const selector of selectors) {{
                  let elements = [];
                  try {{ elements = Array.from(document.querySelectorAll(selector)); }}
                  catch (_) {{ continue; }}
                  let candidateIndex = -1;
                  let candidateScore = -Infinity;
                  elements.forEach((element, index) => {{
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    const text = (element.innerText || element.textContent || '').trim();
                    const mediaSurface = element.matches?.('video')
                      || Boolean(element.querySelector?.('video'));
                    const oversized = rect.width > viewportWidth * 1.75;
                    const eligible = style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && rect.width >= 120
                      && rect.height >= 20
                      && (!oversized || selector === '[data-por-capture-focus]')
                      && (
                        text.length >= 2
                        || (mediaSurface && rect.width >= 240 && rect.height >= 180)
                      );
                    if (!eligible) return;
                    const visibleWidth = Math.max(
                      0,
                      Math.min(viewportWidth, rect.right) - Math.max(0, rect.left)
                    );
                    const visibleRatio = visibleWidth / Math.max(
                      1,
                      Math.min(viewportWidth, rect.width)
                    );
                    const score = visibleRatio * 1000
                      + Math.min(100, text.length / 5)
                      - Math.abs(rect.left) / Math.max(1, viewportWidth);
                    if (score > candidateScore) {{
                      candidateScore = score;
                      candidateIndex = index;
                    }}
                  }});
                  if (candidateIndex >= 0) {{
                    const candidate = elements[candidateIndex];
                    const rect = candidate.getBoundingClientRect();
                    const targetLeftGutter = selector === '[data-por-capture-focus]'
                      ? Math.min(380, Math.max(240, viewportWidth * 0.22))
                      : leftGutter;
                    desiredLeft = targetLeftGutter;
                    const visibleWidth = Math.max(
                      0,
                      Math.min(viewportWidth, rect.right) - Math.max(0, rect.left)
                    );
                    const relevantWidth = Math.max(
                      1,
                      Math.min(viewportWidth, rect.width)
                    );
                    const visibleRatio = visibleWidth / relevantWidth;
                    focusVisibleRatio = visibleRatio;
                    focusLeft = rect.left;
                    focusSelector = selector;
                    focusIndex = candidateIndex;
                    focusFound = true;
                    const needsSidebarGutter = documentWidth > viewportWidth + 32;
                    const minimumLeft = focusX <= 4
                      ? -16
                      : (needsSidebarGutter ? targetLeftGutter - 16 : -32);
                    const wellFramed = visibleRatio >= 0.75
                      && rect.left >= minimumLeft
                      && (
                        !needsSidebarGutter
                        || rect.left <= viewportWidth * 0.45
                      );
                    if (!wellFramed) {{
                      focusX = Math.max(
                        0,
                        Math.min(
                          documentWidth - viewportWidth,
                          rect.left + scrollX - targetLeftGutter
                        )
                      );
                      // Only repair the site's default layout. A reviewer who
                      // intentionally scrolled a wide table stays in control.
                      needsHorizontalAlignment = (scrollX || 0) <= 4
                        && (
                          Math.abs(focusX - (scrollX || 0)) > 8
                          || Math.abs(rect.left - leftGutter) > 8
                        );
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
                  needsHorizontalAlignment,
                  focusSelector,
                  focusIndex,
                  focusFound,
                  desiredLeft,
                  focusVisibleRatio,
                  focusLeft
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
            "focus_selector": str(raw.get("focusSelector") or ""),
            "focus_index": max(0, int(raw.get("focusIndex") or 0)),
            "focus_found": bool(
                raw.get(
                    "focusFound",
                    raw.get("focusSelector") or raw.get("focusX"),
                )
            ),
            "desired_left": max(0, int(raw.get("desiredLeft") or 0)),
            "focus_visible_ratio": max(
                0.0,
                min(1.0, float(raw.get("focusVisibleRatio", 1.0))),
            ),
            "focus_left": int(raw.get("focusLeft") or 0),
            "focus_geometry_measured": (
                "focusVisibleRatio" in raw and "focusLeft" in raw
            ),
        }
    except Exception:  # noqa: BLE001 - geometry probing is best-effort
        return None


async def _reset_browser_zoom(page: Any) -> None:
    """Reset per-origin browser zoom before evidence framing.

    Chromium remembers zoom for an origin inside a live browser session.  A
    reviewer zooming a content page could therefore make the subsequently
    opened Douyin/Kuaishou profile appear massively enlarged.  ``Ctrl+0`` is
    the browser-native reset and does not rewrite the site's transforms/CSS.
    """

    keyboard = getattr(page, "keyboard", None)
    press = getattr(keyboard, "press", None)
    if not callable(press):
        return
    try:
        await press("Control+0")
        if hasattr(page, "wait_for_timeout"):
            await page.wait_for_timeout(80)
    except Exception:  # noqa: BLE001 - zoom normalization is best-effort
        pass
