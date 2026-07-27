"""The immutable worksheet, column, enum and capacity contract for template.xlsx."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SheetLayout:
    name: str
    headers: tuple[str, ...]
    required_columns: frozenset[str]
    field_columns: Mapping[str, str]
    primary_screenshot_column: str | None
    attachment_column: str | None
    validation_values: Mapping[str, tuple[str, ...]]
    formatted_last_row: int
    data_start_row: int = 3

    @property
    def max_rows(self) -> int:
        return self.formatted_last_row - self.data_start_row + 1

    @property
    def column_count(self) -> int:
        return len(self.headers)

    def column_number(self, column: str) -> int:
        return ord(column) - ord("A") + 1


TEXT_TYPES = ("正文", "评论回复")
SHEET_ORDER = ("电商平台", "公众号", "群聊", "朋友圈", "图文视频", "微博博客", "生活资讯", "浏览器")


def _layout(
    name: str,
    headers: tuple[str, ...],
    required_columns: str,
    field_columns: Mapping[str, str],
    primary_screenshot_column: str | None,
    attachment_column: str | None,
    validation_values: Mapping[str, tuple[str, ...]],
    formatted_last_row: int = 201,
) -> SheetLayout:
    return SheetLayout(
        name=name,
        headers=headers,
        required_columns=frozenset(required_columns),
        field_columns=field_columns,
        primary_screenshot_column=primary_screenshot_column,
        attachment_column=attachment_column,
        validation_values=validation_values,
        formatted_last_row=formatted_last_row,
    )


SHEET_LAYOUTS: dict[str, SheetLayout] = {
    "电商平台": _layout(
        "电商平台",
        ("商品URL(必填)", "发布平台(必填)", "商品标题(必填)", "处置对象(必填)", "处置内容(必填)", "店铺名称(必填)", "商品截图文件名(必填)", "其他附件文件名(多个逗号分隔)"),
        "ABCDEFG",
        {"url": "A", "platform": "B", "title": "C", "text_type": "D", "content": "E", "store_name": "F"},
        "G",
        "H",
        {
            "B": ("拼多多", "阿里_天猫_电商平台", "阿里_淘宝_电商平台", "阿里_闲鱼_电商平台", "阿里_1688_电商平台", "京东_京东商城_电商平台", "字节跳动_抖音_电商平台"),
            "D": ("商家", "评论回复"),
        },
        formatted_last_row=4,
    ),
    "公众号": _layout(
        "公众号",
        ("文章链接(必填)", "发布平台(必填)", "文章标题(必填)", "公众号微信号(必填)", "公众号UIN", "公众号名称", "处置对象(必填)", "信息内容(必填)", "发布时间(yyyy-mm-dd HH:mm:ss)", "文章截图文件名(必填)", "其他附件文件名(多个逗号分隔)"),
        "ABCDGHJ",
        {"url": "A", "platform": "B", "title": "C", "author_id": "D", "account_uin": "E", "author_name": "F", "text_type": "G", "content": "H", "published_at": "I"},
        "J",
        "K",
        {"B": ("微信-公众号", "百度_百家号_公众号"), "G": TEXT_TYPES},
    ),
    "群聊": _layout(
        "群聊",
        ("群号", "群名称", "发布平台(必填)", "用户账号", "用户id", "信息内容(必填)", "发布时间(yyyy-mm-dd HH:mm:ss)", "群聊截图文件名", "其他附件文件名(多个逗号分隔)"),
        "CF",
        {"platform": "C", "author_name": "D", "author_id": "E", "content": "F", "published_at": "G"},
        "H",
        "I",
        {"C": ("微信-群聊", "QQ-群聊")},
    ),
    "朋友圈": _layout(
        "朋友圈",
        ("用户账号(必填)", "用户id", "发布平台(必填)", "发布时间(yyyy-mm-dd HH:mm:ss)", "信息内容(必填)", "朋友圈截图文件名", "其他附件文件名(多个逗号分隔)"),
        "ACE",
        {"author_name": "A", "author_id": "B", "platform": "C", "published_at": "D", "content": "E"},
        "F",
        "G",
        {"C": ("微信-朋友圈",)},
    ),
    "图文视频": _layout(
        "图文视频",
        ("URL(如果发布平台是腾讯_微信_图文视频，该项非必填，否则必填)", "用户账号(必填)", "昵称(必填)", "发布平台(必填)", "文本类型(必填)", "发布时间(yyyy-mm-dd HH:mm:ss)", "信息内容(必填)", "账号截图名(必填)", "其他文件名(多个逗号分隔)"),
        "BCDEGH",
        {"url": "A", "author_id": "B", "author_name": "C", "platform": "D", "text_type": "E", "published_at": "F", "content": "G"},
        "H",
        "I",
        {"D": ("快手科技_快手_图文视频", "行吟科技_小红书_图文视频", "字节跳动_抖音_图文视频", "幻电科技_哔哩哔哩_图文视频", "腾讯_微信_图文视频", "搜狐_搜狐视频_图文视频", "阿里巴巴_土豆_图文视频", "阿里巴巴_优酷_图文视频", "字节跳动_西瓜视频_图文视频", "爱奇艺_爱奇艺_图文视频"), "E": TEXT_TYPES},
    ),
    "微博博客": _layout(
        "微博博客",
        ("URL(必填)", "昵称(必填)", "发布平台(必填)", "文本类型(必填)", "发布时间(yyyy-mm-dd HH:mm:ss)", "信息内容(必填)", "网页截图名(必填)", "其他文件名(多个逗号分隔)"),
        "ABCDFG",
        {"url": "A", "author_name": "B", "platform": "C", "text_type": "D", "published_at": "E", "content": "F"},
        "G",
        "H",
        {"C": ("新浪_新浪微博_博客贴吧", "百度_百度贴吧_博客贴吧", "知乎_知乎_博客贴吧"), "D": TEXT_TYPES},
    ),
    "生活资讯": _layout(
        "生活资讯",
        ("URL(必填)", "昵称(必填)", "发布平台(必填)", "文本类型(必填)", "用户账号", "发布时间(yyyy-mm-dd HH:mm:ss)", "信息内容(必填)", "网页截图名(必填)", "其他文件名(多个逗号分隔)"),
        "ABCDFGH",
        {"url": "A", "author_name": "B", "platform": "C", "text_type": "D", "author_id": "E", "published_at": "F", "content": "G"},
        "H",
        "I",
        {"C": ("字节跳动_今日头条_生活资讯", "网易_网易新闻_生活资讯", "凤凰网_凤凰新闻_生活资讯", "搜狐_搜狐新闻_生活资讯", "搜狐_狐友_生活资讯", "虎扑_虎扑_生活资讯", "三快_美团_生活资讯", "字节跳动_懂车帝_生活资讯"), "D": TEXT_TYPES},
    ),
    "浏览器": _layout(
        "浏览器",
        ("URL(必填)", "用户账号(必填)", "昵称(必填)", "发布平台(必填)", "文本类型(必填)", "发布时间(yyyy-mm-dd HH:mm:ss)", "信息内容(必填)", "账号截图名(必填)", "其他文件名(多个逗号分隔)"),
        "ABCDEFGH",
        {"url": "A", "author_id": "B", "author_name": "C", "platform": "D", "text_type": "E", "published_at": "F", "content": "G"},
        "H",
        "I",
        {"D": ("阿里巴巴_UC浏览器_浏览器", "360_360浏览器_浏览器", "华为_华为浏览器_浏览器", "腾讯_QQ浏览器_浏览器"), "E": TEXT_TYPES},
    ),
}


def get_sheet_layout(name: str) -> SheetLayout:
    try:
        return SHEET_LAYOUTS[name]
    except KeyError as error:
        raise ValueError(f"Unsupported template worksheet: {name}") from error


def expected_validation_formula(values: tuple[str, ...]) -> str:
    return ",".join(values)
