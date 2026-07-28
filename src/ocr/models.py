"""Typed results returned by the isolated OCR worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.domain.models import OcrStatus


@dataclass(frozen=True)
class OcrImageResult:
    path: Path
    status: OcrStatus
    text: str = ""
    confidence: float | None = None
    error: str = ""


@dataclass(frozen=True)
class OcrBatchResult:
    status: OcrStatus
    images: tuple[OcrImageResult, ...] = ()
    error: str = ""

    @property
    def text(self) -> str:
        return "\n\n".join(
            image.text.strip()
            for image in self.images
            if image.status == OcrStatus.SUCCESS and image.text.strip()
        )

    @property
    def text_image_count(self) -> int:
        return sum(image.status == OcrStatus.SUCCESS for image in self.images)

    @classmethod
    def unavailable(
        cls,
        paths: list[Path],
        message: str,
    ) -> "OcrBatchResult":
        return cls(
            OcrStatus.UNAVAILABLE,
            tuple(
                OcrImageResult(path, OcrStatus.UNAVAILABLE, error=message)
                for path in paths
            ),
            message,
        )
