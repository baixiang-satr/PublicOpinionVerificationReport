"""Extract text from images using OCR, with RapidOCR backend.

This module provides a reusable OCR tool for extracting text from image
files.  It is designed to be called both as part of the crawl pipeline
(when a page has no HTML text content) and standalone for future needs.

Usage:
    text = extract_text_from_image(Path("screenshot.png"))
    # Returns extracted text or "无文字" if nothing found.

    all_text = extract_text_from_images([Path("img1.png"), Path("img2.jpg")])
    # Concatenates results with newline separators.
"""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)


class OcrError(RuntimeError):
    """An OCR-specific error that does not break the caller's control flow."""


# ---------------------------------------------------------------------------
# Lazy-loaded singleton — the model is only loaded on first call.
# ---------------------------------------------------------------------------

_OCR_UNAVAILABLE = object()
_ocr_instance: Any = None


def _get_engine() -> Any | None:
    """Return RapidOCR when available, otherwise a cached graceful fallback."""

    global _ocr_instance  # noqa: PLW0603
    if _ocr_instance is _OCR_UNAVAILABLE:
        return None
    if _ocr_instance is None:
        if not _ocr_backend_is_importable():
            logger.warning("RapidOCR runtime probe failed; OCR will be skipped.")
            _ocr_instance = _OCR_UNAVAILABLE
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]

            _ocr_instance = RapidOCR()
        except Exception as error:
            logger.warning("RapidOCR unavailable; OCR will be skipped: %s", error)
            _ocr_instance = _OCR_UNAVAILABLE
            return None
    return _ocr_instance


def _ocr_backend_is_importable() -> bool:
    """Probe the native ONNX DLL in a disposable process before loading it."""

    # The currently supported onnxruntime Windows wheel can report a successful
    # standalone import on Python 3.14 and then fail while loading its DLL in a
    # long-lived Qt/pytest process. Skip the optional backend instead of risking
    # an access violation; DOM extraction and normal screenshots still work.
    if sys.version_info >= (3, 14):
        return False
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import rapidocr_onnxruntime"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_text_from_image(
    image_path: Path,
    *,
    confidence_threshold: float = 0.5,
) -> str:
    """Run OCR on a single image and return the recognised text.

    Parameters
    ----------
    image_path:
        Absolute or relative path to a raster image (PNG, JPEG, etc.)
        supported by RapidOCR.
    confidence_threshold:
        Minimum confidence score (0-1) for a text block to be included.
        Blocks below this threshold are silently dropped.

    Returns
    -------
    str
        The concatenated recognised text, or ``"无文字"`` when the image
        contains no legible text above the confidence threshold.

    Raises
    ------
    OcrError
        If the engine cannot be loaded or the image cannot be read.
    """
    _validate_image(image_path)
    engine = _get_engine()
    if engine is None:
        return "无文字"
    try:
        result, _ = engine(str(image_path))
    except Exception as error:
        raise OcrError(f"OCR failed for {image_path.name}: {error}") from error

    if not result:
        return "无文字"

    lines: list[str] = []
    for block in result:
        # block is typically (bbox, text, confidence)
        if len(block) >= 3:
            text = str(block[1]).strip()
            confidence = float(block[2])
            if text and confidence >= confidence_threshold:
                lines.append(text)
        elif len(block) >= 2:
            text = str(block[1]).strip()
            if text:
                lines.append(text)

    return "\n".join(lines) if lines else "无文字"


def _validate_image(image_path: Path) -> None:
    path = Path(image_path)
    if not path.is_file():
        raise OcrError(f"OCR image does not exist: {path}")
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
    except Exception as error:
        raise OcrError(f"OCR image is invalid: {path.name}") from error


def extract_text_from_images(
    image_paths: list[Path],
    *,
    confidence_threshold: float = 0.5,
) -> str:
    """Run OCR on multiple images and concatenate results.

    Each image's recognised text is separated by two newlines so that
    content from different images remains distinguishable.

    Parameters
    ----------
    image_paths:
        One or more paths to raster images.
    confidence_threshold:
        Minimum confidence score forwarded to *extract_text_from_image*.

    Returns
    -------
    str
        Concatenated text from all images, or ``"无文字"`` if all images
        returned no text.
    """
    parts: list[str] = []
    for path in image_paths:
        try:
            text = extract_text_from_image(path, confidence_threshold=confidence_threshold)
            if text and text != "无文字":
                parts.append(text)
        except OcrError:
            logger.warning("Skipping OCR for %s due to error", path.name)
            continue

    return "\n\n".join(parts) if parts else "无文字"
