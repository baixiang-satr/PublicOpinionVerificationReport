"""
全局配置模块 — 区分固定模板配置与每次任务的可覆盖配置。

固定项包括 template 目录、template.xlsx 和 template.zip；它们不得由 GUI 改写。
任务项包括并发、超时、截图格式、图片上限和用户显式提供的登录态。
"""


class AppConfig:
    """应用程序配置入口；运行期不使用模块级可变全局状态。"""
    pass
