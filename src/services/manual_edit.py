"""人工单元格编辑的前置校验：保证「保存成功 = 导出时同样生效」。

校验器与导出侧（``src.services.override_apply``）共用同一套解析器与
工作表允许值，从机制上消灭「保存成功但导出静默保留原值」的分歧。
校验失败抛 ``ValueError``，消息面向操作员、可直接弹窗展示。
"""
from __future__ import annotations

from src.domain.template_schema import SheetLayout
from src.services.override_apply import parse_manual_datetime

_ENUM_FIELDS = ("text_type", "platform")
_FIELD_LABELS = {"text_type": "文本类型", "platform": "发布平台"}


def validate_manual_edit(
    layout: SheetLayout | None,
    field: str,
    value: str,
) -> None:
    """校验一次单元格编辑；非法值抛 ``ValueError``（空值一律放行）。

    空值语义由 ``ManualOverrideStore.set_field`` 定义（显式清空/回退
    自动识别值），任何字段都允许为空，因此这里只校验非空输入。
    """

    text = value.strip()
    if not text:
        return
    if field == "published_at":
        if parse_manual_datetime(text) is None:
            raise ValueError(
                f"发布时间 {text!r} 无法识别，请改用 2026-09-01 或 "
                "2026-09-01 10:30 等格式。"
            )
        return
    if field in _ENUM_FIELDS and layout is not None:
        column = layout.field_columns.get(field)
        allowed = layout.validation_values.get(column or "", ())
        if allowed and text not in allowed:
            label = _FIELD_LABELS.get(field, field)
            raise ValueError(
                f"{label} {text!r} 不在本工作表允许值内：{'、'.join(allowed)}。"
            )


__all__ = ["validate_manual_edit"]
