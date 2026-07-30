"""Shared pixel-level sanity checks for evidence screenshots.

Blank or near-uniform captures are never usable as evidence.  The check is
shared by the automated :class:`~src.screenshot.page_shooter.PageShooter`
and the interactive region-capture tool so both apply the same bar.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError


class UnreadableImageError(ValueError):
    """Raised when a screenshot file cannot be decoded as an image."""


def is_visually_blank(path: Path) -> bool:
    """True when the image is near-uniform (blank captures are not evidence)."""

    try:
        with Image.open(path) as source:
            image = source.convert("L")
            minimum_tone, maximum_tone = image.getextrema()
            image.thumbnail((256, 256))
            standard_deviation = float(ImageStat.Stat(image).stddev[0])
            entropy = float(image.entropy())
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise UnreadableImageError(
            f"Screenshot file is not a readable image: {error}"
        ) from error
    return (
        standard_deviation < 5.0
        and entropy < 1.0
        and maximum_tone - minimum_tone < 32
    )
