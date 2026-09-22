"""「函」文档资产：统一入口选择的函图片（可多份 .jpg）随交付包导出。

用户在抓取开始前选择函图片（如 ``XX市申请处置的函.jpg``，可多选），平台负责：

1. 抓取/导出时把函文件复制进 staging ``template/`` 目录（打包校验要求
   附件列引用的文件名必须在 zip 内真实存在）；
2. 同时持久化到任务目录 ``letter/`` 子目录，断点续跑、补录重导出与
   zip 导入重开任务后仍能被发现；持久化目录与最近一次选择保持一致
   （不在本次选择中的旧函会被移除）；
3. 全部函名追加到每一行附件列末尾（英文逗号连接，去重、避开主截图名）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
import shutil
from pathlib import Path

from src.domain.models import TemplateRow
from src.domain.template_schema import get_sheet_layout
from src.utils.file_utils import require_safe_file_name

LETTER_DIR_NAME = "letter"


def _deduped_pairs(letter_paths: Iterable[Path]) -> list[tuple[Path, str]]:
    """按安全文件名去重（同名后者覆盖前者，列值只保留一份）。"""

    pairs: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for letter_path in letter_paths:
        name = require_safe_file_name(Path(letter_path).name)
        if name in seen:
            continue
        seen.add(name)
        pairs.append((Path(letter_path), name))
    return pairs


def stage_letter_files(template_dir: Path, letter_paths: Iterable[Path]) -> tuple[str, ...]:
    """把函文件复制进 staging template 目录，返回安全文件名元组。"""

    names: list[str] = []
    for source, name in _deduped_pairs(letter_paths):
        shutil.copy2(source, Path(template_dir) / name)
        names.append(name)
    return tuple(names)


def persist_letter_files(job_dir: Path, letter_paths: Iterable[Path]) -> tuple[str, ...]:
    """把函文件持久化到任务目录 letter/ 子目录（同名覆盖，移除不在本次选择中的旧函）。"""

    pairs = _deduped_pairs(letter_paths)
    keep = {name for _, name in pairs}
    target_dir = Path(job_dir) / LETTER_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    for stale in target_dir.iterdir():
        if stale.is_file() and stale.name not in keep:
            stale.unlink()
    for source, name in pairs:
        shutil.copy2(source, target_dir / name)
    return tuple(name for _, name in pairs)


def find_staged_letters(job_dir: Path) -> tuple[Path, ...]:
    """在任务目录 letter/ 子目录发现此前持久化的全部函文件。"""

    target_dir = Path(job_dir) / LETTER_DIR_NAME
    if not target_dir.is_dir():
        return ()
    return tuple(sorted(path for path in target_dir.iterdir() if path.is_file()))


def append_letter_to_rows(rows: list[TemplateRow], letter_names: Iterable[str]) -> list[TemplateRow]:
    """把全部函名追加到每行附件列末尾。

    既有同名引用先移除再追加到末尾，保证「重复选择同名文件不产生重复
    列值」且函名恒在附件列最后；没有附件列的表保持原样。
    """

    safe_names: list[str] = []
    for letter_name in letter_names:
        safe_name = require_safe_file_name(letter_name)
        if safe_name not in safe_names:
            safe_names.append(safe_name)
    if not safe_names:
        return rows
    blocked = set(safe_names)
    updated: list[TemplateRow] = []
    for row in rows:
        layout = get_sheet_layout(row.sheet_name)
        if not layout.attachment_column:
            updated.append(row)
            continue
        names = [
            name
            for name in row.attachment_names
            if name and name not in blocked and name != row.primary_screenshot_name
        ]
        names.extend(safe_names)
        values = dict(row.values_by_column)
        values[layout.attachment_column] = ",".join(names)
        updated.append(
            replace(row, values_by_column=values, attachment_names=tuple(names))
        )
    return updated


__all__ = [
    "LETTER_DIR_NAME",
    "append_letter_to_rows",
    "find_staged_letters",
    "persist_letter_files",
    "stage_letter_files",
]
