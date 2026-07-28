"""JSON Lines serialization shared by the OCR client and worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.domain.models import OcrStatus
from src.ocr.models import OcrBatchResult, OcrImageResult


def recognition_request(
    request_id: str,
    paths: list[Path],
    confidence_threshold: float,
) -> dict[str, Any]:
    return {
        "type": "recognize",
        "request_id": request_id,
        "paths": [str(path.resolve()) for path in paths],
        "confidence_threshold": confidence_threshold,
    }


def parse_response(payload: dict[str, Any]) -> OcrBatchResult:
    status = _status(payload.get("status"))
    images = tuple(
        OcrImageResult(
            Path(str(item.get("path") or "")),
            _status(item.get("status")),
            str(item.get("text") or ""),
            (
                float(item["confidence"])
                if item.get("confidence") is not None
                else None
            ),
            str(item.get("error") or ""),
        )
        for item in payload.get("images") or ()
        if isinstance(item, dict)
    )
    return OcrBatchResult(status, images, str(payload.get("error") or ""))


def _status(value: Any) -> OcrStatus:
    try:
        return OcrStatus(str(value))
    except ValueError:
        return OcrStatus.FAILED
