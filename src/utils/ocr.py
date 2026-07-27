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
from typing import Any

logger = logging.getLogger(__name__)


class OcrError(RuntimeError):
    """An OCR-specific error that does not break the caller's control flow."""


# ---------------------------------------------------------------------------
# Lazy-loaded singleton — the model is only loaded on first call.
# ---------------------------------------------------------------------------

_ocr_instance: Any | None = None


def _get_engine() -> Any:
    """Return the shared RapidOCR engine, loading it on first access."""
    global _ocr_instance  # noqa: PLW0603
    if _ocr_instance is None:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]

            _ocr_instance = RapidOCR()
        except ImportError as error:
            raise OcrError(
                "RapidOCR is not installed. Run: pip install rapidocr-onnxruntime"
            ) from error
        except Exception as error:
            raise OcrError(f"Failed to initialise RapidOCR engine: {error}") from error
    return _ocr_instance


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
    engine = _get_engine()
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
