from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.release_check import run_checks


def test_release_check_validates_current_source_and_template() -> None:
    project_root = Path(__file__).resolve().parents[1]

    report = run_checks(project_root)

    assert report.python_files > 0
    assert report.markdown_files >= 5
    assert report.template_files > 0
    assert len(report.template_fingerprint) == 64


def test_release_check_rejects_unsafe_archive_entry(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    archive_path = tmp_path / "template.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("template/template.xlsx", b"workbook")
        archive.writestr("../outside.txt", b"bad")

    with pytest.raises(RuntimeError, match="unsafe archive entry"):
        run_checks(project_root, archive_path)


def test_release_check_accepts_fixed_archive_layout(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    archive_path = tmp_path / "template.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("template/template.xlsx", b"workbook")
        archive.writestr("template/001.png", b"image")

    report = run_checks(project_root, archive_path)

    assert report.archive_files == 2
