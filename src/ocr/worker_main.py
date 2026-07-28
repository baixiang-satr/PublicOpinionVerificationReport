"""Persistent RapidOCR worker intended to run under Python 3.12."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    engine: Any = None
    engine_error = ""
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
    except Exception as error:
        engine_error = f"{type(error).__name__}: {error}"

    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if request.get("type") == "shutdown":
                return 0
            response = _recognize(request, engine, engine_error)
        except Exception as error:
            response = {
                "request_id": "",
                "status": "failed",
                "images": [],
                "error": f"{type(error).__name__}: {error}",
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def _recognize(
    request: dict[str, Any],
    engine: Any,
    engine_error: str,
) -> dict[str, Any]:
    request_id = str(request.get("request_id") or "")
    paths = [Path(str(value)) for value in request.get("paths") or ()]
    if engine is None:
        return {
            "request_id": request_id,
            "status": "unavailable",
            "images": [
                {
                    "path": str(path),
                    "status": "unavailable",
                    "error": engine_error or "RapidOCR unavailable",
                }
                for path in paths
            ],
            "error": engine_error or "RapidOCR unavailable",
        }
    threshold = float(request.get("confidence_threshold", 0.5))
    results = [_recognize_image(engine, path, threshold) for path in paths]
    statuses = {item["status"] for item in results}
    if "success" in statuses:
        status = "success"
    elif statuses == {"no_text"} or not statuses:
        status = "no_text"
    elif "timeout" in statuses:
        status = "timeout"
    else:
        status = "failed"
    return {
        "request_id": request_id,
        "status": status,
        "images": results,
        "error": "",
    }


def _recognize_image(
    engine: Any,
    path: Path,
    threshold: float,
) -> dict[str, Any]:
    try:
        _validate_image(path)
        raw_result, _elapsed = engine(str(path))
        lines: list[str] = []
        confidences: list[float] = []
        for block in raw_result or ():
            if len(block) < 2:
                continue
            text = str(block[1]).strip()
            confidence = float(block[2]) if len(block) >= 3 else 1.0
            if text and confidence >= threshold:
                lines.append(text)
                confidences.append(confidence)
        if not lines:
            return {"path": str(path), "status": "no_text", "text": ""}
        return {
            "path": str(path),
            "status": "success",
            "text": "\n".join(lines),
            "confidence": sum(confidences) / len(confidences),
        }
    except Exception as error:
        return {
            "path": str(path),
            "status": "failed",
            "text": "",
            "error": f"{type(error).__name__}: {error}",
        }


def _validate_image(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    from PIL import Image

    with Image.open(path) as image:
        image.verify()


if __name__ == "__main__":
    raise SystemExit(main())
