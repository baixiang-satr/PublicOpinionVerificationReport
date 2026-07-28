"""Best-effort page-level browser fingerprint compatibility patches."""

from __future__ import annotations

from typing import Any


async def apply_extra_stealth(page: Any) -> None:
    """Apply the existing optional JavaScript patches to a new page."""

    try:
        script = """
            () => {
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true,
                });

                if (window.chrome) {
                    Object.defineProperty(window.chrome, 'runtime', {
                        get: () => ({
                            connect: () => null,
                            sendMessage: () => null,
                            onMessage: { addListener: () => {} },
                            onConnect: { addListener: () => {} },
                        }),
                        configurable: true,
                    });
                }

                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en'],
                    configurable: true,
                });

                const originalQuery = window.navigator.permissions?.query;
                if (originalQuery) {
                    window.navigator.permissions.query = (desc) => {
                        if (desc.name === 'notifications') {
                            return Promise.resolve({ state: 'denied' });
                        }
                        return originalQuery.call(
                            window.navigator.permissions,
                            desc
                        );
                    };
                }
            }
        """
        await page.add_init_script(script=f"({script})()")
        await page.evaluate(script)
    except Exception:
        # Optional compatibility patches must never fail page creation.
        pass
