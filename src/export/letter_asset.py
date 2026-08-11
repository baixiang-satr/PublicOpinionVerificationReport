"""「函」文档资产：统一入口选择的函文件随交付包导出。

用户在抓取开始前选择一次函文件（如 ``XX市申请处置的函.docx``），平台负责：

1. 抓取/导出时把函文件复制进 staging ``template/`` 目录（打包校验要求
   附件列引用的文件名必须在 zip 内真实存在）；
2. 同时持久化到任务目录 ``letter/`` 子目录，断点续跑、补录重导出与
   zip 导入重开任务后仍能被发现；
3. 函名追加到每一行附件列末尾（英文逗号连接，去重、避开主截图名）。
"""

from __future__ import annotations

from dataclasses import replace
import shutil
from pathlib import Path

from src.domain.models import TemplateRow
from src.domain.template_schema import get_sheet_layout
from src.utils.file_utils import require_safe_file_name

LETTER_DIR_NAME = "letter"


def stage_letter_file(template_dir: Path, letter_path: Path) -> str:
    """把函文件复制进 staging template 目录，返回安全文件名。"""

    name = require_safe_file_name(Path(letter_path).name)
    shutil.copy2(letter_path, Path(template_dir) / name)
    return name


def persist_letter_file(job_dir: Path, letter_path: Path) -> str:
    """把函文件持久化到任务目录 letter/ 子目录（同目录只保留一份）。"""

    name = require_safe_file_name(Path(letter_path).name)
    target_dir = Path(job_dir) / LETTER_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    for stale in target_dir.iterdir():
        if stale.is_file() and stale.name != name:
            stale.unlink()
    shutil.copy2(letter_path, target_dir / name)
    return name


def find_staged_letter(job_dir: Path) -> Path | None:
    """在任务目录 letter/ 子目录发现此前持久化的函文件。"""

    target_dir = Path(job_dir) / LETTER_DIR_NAME
    if not target_dir.is_dir():
        return None
    candidates = sorted(path for path in target_dir.iterdir() if path.is_file())
    return candidates[0] if candidates else None


def append_letter_to_rows(rows: list[TemplateRow], letter_name: str) -> list[TemplateRow]:
    """把函名追加到每行附件列末尾。

    既有同名引用先移除再追加到末尾，保证「重复选择同名文件不产生重复
    列值」且函名恒在附件列最后；没有附件列的表保持原样。
    """

    safe_name = require_safe_file_name(letter_name)
    updated: list[TemplateRow] = []
    for row in rows:
        layout = get_sheet_layout(row.sheet_name)
        if not layout.attachment_column:
            updated.append(row)
            continue
        names = [
            name
            for name in row.attachment_names
            if name and name != safe_name and name != row.primary_screenshot_name
        ]
        names.append(safe_name)
        values = dict(row.values_by_column)
        values[layout.attachment_column] = ",".join(names)
        updated.append(
            replace(row, values_by_column=values, attachment_names=tuple(names))
        )
    return updated


__all__ = [
    "LETTER_DIR_NAME",
    "append_letter_to_rows",
    "find_staged_letter",
    "persist_letter_file",
    "stage_letter_file",
]
