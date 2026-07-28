from pathlib import Path

from src.domain.models import OcrStatus
from src.ocr.client import OcrClient


def test_missing_worker_is_unavailable_not_no_text(tmp_path: Path) -> None:
    image = tmp_path / "poster.png"
    image.write_bytes(b"not-used")
    client = OcrClient(tmp_path / "missing-python.exe")

    result = client.recognize(
        [image],
        confidence_threshold=0.5,
    )

    assert result.status == OcrStatus.UNAVAILABLE
    assert result.images[0].status == OcrStatus.UNAVAILABLE
    assert "Python 3.12" in result.error
