"""桥接层许可证守卫与 js_api 方法（独立模块，避免 bridge.py 超 500 行）。

- ``default_license_manager``：生产环境默认管理器工厂（测试可 monkeypatch 替身）；
- ``requires_license``：业务操作守卫装饰器，未激活返回 ``LICENSE_REQUIRED``；
- ``LicenseApiMixin``：``license_status / license_activate / license_deactivate``
  三个 js_api 方法，由 ``WebUIBridge`` 继承（pywebview 可暴露继承的方法）。
"""

from __future__ import annotations

import functools
from typing import Protocol

from src.license.manager import LicenseManager


def default_license_manager() -> LicenseManager:
    """生产环境默认授权管理器（测试注入替身时不会触达真实存储/指纹）。"""

    return LicenseManager()


class _LicensedHost(Protocol):
    license: LicenseManager


def requires_license(method):
    """业务操作守卫：未激活或授权无效时返回 LICENSE_REQUIRED。"""

    @functools.wraps(method)
    def wrapper(self: _LicensedHost, *args, **kwargs):
        info = self.license.status()
        if not info.activated:
            return {"ok": False, "code": "LICENSE_REQUIRED", "message": info.message}
        return method(self, *args, **kwargs)

    return wrapper


class LicenseApiMixin:
    """许可证相关 js_api 方法，宿主类需提供 ``self.license``。"""

    license: LicenseManager

    def license_status(self) -> dict:
        return self.license.status().to_payload()

    def license_activate(self, code: str) -> dict:
        return self.license.activate(code or "").to_payload()

    def license_deactivate(self) -> dict:
        return self.license.deactivate().to_payload()


_GUARDED_METHODS = (
    "pick_input_file",
    "pick_zip_file",
    "start_crawl",
    "retry_failed",
    "resume_checkpoint",
    "export_zip",
    "start_region_capture",
    "auth_login",
)


def apply_license_guard(cls) -> type:
    """为宿主类的业务入口批量套上许可证守卫（未激活返回 LICENSE_REQUIRED）。"""

    for name in _GUARDED_METHODS:
        setattr(cls, name, requires_license(getattr(cls, name)))
    return cls


__all__ = ["LicenseApiMixin", "apply_license_guard", "default_license_manager", "requires_license"]
