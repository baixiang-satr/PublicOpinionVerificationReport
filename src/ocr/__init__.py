"""Isolated OCR worker protocol and client."""

from src.ocr.client import OcrCancelled, OcrClient
from src.ocr.models import OcrBatchResult, OcrImageResult

__all__ = [
    "OcrBatchResult",
    "OcrCancelled",
    "OcrClient",
    "OcrImageResult",
]
