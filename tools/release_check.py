"""Run deterministic source, documentation, template and archive release checks."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sys
from zipfile import ZipFile


MAX_CODE_LINES = 500
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


@dataclass(frozen=True)
class ReleaseReport:
    python_files: int
    markdown_files: int
    template_files: int
    template_fingerprint: str
    archive_files: int = 0


def run_checks(project_root: Path, archive_path: Path | None = None) -> ReleaseReport:
    root = project_root.resolve()
    errors: list[str] = []
    python_files = sorted(
        path
        for folder in ("src", "tests", "tools")
        for path in (root / folder).rglob("*.py")
        if folder != "tests" or ".pytest" not in path.parts
    )
    for path in python_files:
        text = path.read_text(encoding="utf-8")
        line_count = len(text.splitlines())
        if line_count > MAX_CODE_LINES:
            errors.append(f"{path.relative_to(root)} has {line_count} lines")
        try:
            ast.parse(text, filename=str(path))
        except SyntaxError as error:
            errors.append(f"{path.relative_to(root)}: {error}")

    markdown_files = sorted(
        path
        for path in root.rglob("*.md")
        if not _ignored(path.relative_to(root))
    )
    for path in markdown_files:
        errors.extend(_broken_links(path, root))

    template_dir = root / "template"
    workbook = template_dir / "template.xlsx"
    if not workbook.is_file():
        errors.append("template/template.xlsx is missing")
    template_files = sorted(
        path
        for path in template_dir.rglob("*")
        if path.is_file() and not path.name.startswith("~$")
    )
    fingerprint = _manifest_fingerprint(template_dir, template_files)

    archive_files = 0
    if archive_path is not None:
        archive_errors, archive_files = _check_archive(archive_path.resolve())
        errors.extend(archive_errors)
    if errors:
        raise RuntimeError("Release checks failed:\n- " + "\n- ".join(errors))
    return ReleaseReport(
        python_files=len(python_files),
        markdown_files=len(markdown_files),
        template_files=len(template_files),
        template_fingerprint=fingerprint,
        archive_files=archive_files,
    )


def _broken_links(markdown_path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    text = markdown_path.read_text(encoding="utf-8")
    for match in MARKDOWN_LINK.finditer(text):
        target = match.group(1).strip().strip("<>")
        if (
            not target
            or target.startswith(("#", "http://", "https://", "mailto:"))
            or "://" in target
        ):
            continue
        relative_target = target.split("#", 1)[0].split(":", 1)[0]
        if not relative_target:
            continue
        resolved = (markdown_path.parent / relative_target).resolve()
        if not resolved.exists():
            errors.append(
                f"{markdown_path.relative_to(root)} -> missing {relative_target}"
            )
    return errors


def _manifest_fingerprint(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _check_archive(path: Path) -> tuple[list[str], int]:
    if not path.is_file():
        return ([f"archive is missing: {path}"], 0)
    errors: list[str] = []
    with ZipFile(path) as archive:
        names = archive.namelist()
    if len(names) != len(set(names)):
        errors.append("archive contains duplicate names")
    if "template/template.xlsx" not in names:
        errors.append("archive is missing template/template.xlsx")
    for name in names:
        parts = Path(name).parts
        if not name.startswith("template/") or ".." in parts or Path(name).is_absolute():
            errors.append(f"unsafe archive entry: {name}")
        if any(part in {"logs", "runtime", "__pycache__"} for part in parts):
            errors.append(f"runtime file leaked into archive: {name}")
    return errors, len(names)


def _ignored(relative: Path) -> bool:
    return bool(
        set(relative.parts)
        & {".git", ".venv", "references", "output", "__pycache__"}
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        report = run_checks(args.project, args.archive)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    print(
        "release-check-ok "
        f"python={report.python_files} markdown={report.markdown_files} "
        f"template={report.template_files} archive={report.archive_files} "
        f"template_sha256={report.template_fingerprint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
