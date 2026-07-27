"""Unit tests for the OCR utility (src/utils/ocr.py).

These tests use synthetic images generated in memory — no external image
files are needed.  The RapidOCR model is loaded lazily, so the first test
that triggers a real OCR call will download/load the model.  Tests that
only assert error paths do not load the model at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.ocr import OcrError, extract_text_from_image, extract_text_from_images


# =========================================================================
# Helpers — minimal synthetic images
# =========================================================================


def _make_image(width: int, height: int, fmt: str = "PNG", gradient: bool = False) -> bytes:
    """Build a synthetic image using Pillow."""
    from io import BytesIO  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    if gradient:
        img = Image.new("RGB", (width, height))
        for y in range(height):
            for x in range(width):
                intensity = (x + y) % 256
                img.putpixel((x, y), (intensity, intensity, intensity))
    else:
        img = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def text_image(tmp_path: Path) -> Path:
    """A non-uniform gradient PNG — gives OCR engine something to process."""
    path = tmp_path / "with_text.png"
    path.write_bytes(_make_image(200, 50, fmt="PNG", gradient=True))
    return path


@pytest.fixture
def blank_image(tmp_path: Path) -> Path:
    """A uniform white PNG — no text expected."""
    path = tmp_path / "blank.png"
    path.write_bytes(_make_image(100, 40, fmt="PNG"))
    return path


@pytest.fixture
def jpeg_image(tmp_path: Path) -> Path:
    """A minimal valid JPEG."""
    path = tmp_path / "test.jpg"
    path.write_bytes(_make_image(32, 32, fmt="JPEG"))
    return path


@pytest.fixture
def invalid_file(tmp_path: Path) -> Path:
    """Return a path that points to a non-image file."""
    path = tmp_path / "not_an_image.txt"
    path.write_text("This is not an image.")
    return path


# =========================================================================
# Tests — extract_text_from_image
# =========================================================================


class TestExtractTextFromImage:
    def test_non_empty_result(self, text_image: Path) -> None:
        """Should return a string (text or '无文字') without raising."""
        result = extract_text_from_image(text_image)
        assert isinstance(result, str)
        # The actual content depends on the OCR engine; we only check
        # that the call succeeds and returns something reasonable.
        assert len(result) > 0

    def test_blank_image_returns_no_text(self, blank_image: Path) -> None:
        """A uniform blank image should return '无文字'."""
        result = extract_text_from_image(blank_image)
        assert result == "无文字"

    def test_jpeg_image(self, jpeg_image: Path) -> None:
        """JPEG images should be processed without error."""
        result = extract_text_from_image(jpeg_image)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_confidence_threshold_param(self, text_image: Path) -> None:
        """confidence_threshold parameter is accepted without error."""
        result = extract_text_from_image(text_image, confidence_threshold=0.0)
        assert isinstance(result, str)
        result2 = extract_text_from_image(text_image, confidence_threshold=0.9)
        assert isinstance(result2, str)

    def test_invalid_path_raises(self) -> None:
        """A non-existent path should raise OcrError."""
        with pytest.raises(OcrError):
            extract_text_from_image(Path("does_not_exist.png"))

    def test_invalid_file_raises(self, invalid_file: Path) -> None:
        """A non-image file should raise OcrError."""
        with pytest.raises(OcrError):
            extract_text_from_image(invalid_file)


# =========================================================================
# Tests — extract_text_from_images (multi-image)
# =========================================================================


class TestExtractTextFromImages:
    def test_single_image(self, text_image: Path) -> None:
        """Should behave the same as extract_text_from_image for a single image."""
        result = extract_text_from_images([text_image])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_multiple_images(self, text_image: Path, blank_image: Path) -> None:
        """Should process multiple images and return a string without error."""
        result = extract_text_from_images([text_image, blank_image])
        assert isinstance(result, str)
        # RapidOCR may or may not detect "text" in a gradient; the key
        # assertion is that the call succeeds and returns a valid result.
        assert len(result) > 0

    def test_all_blank_returns_no_text(self, blank_image: Path) -> None:
        """If all images are blank, result should be '无文字'."""
        result = extract_text_from_images([blank_image, blank_image])
        assert result == "无文字"

    def test_empty_list_returns_no_text(self) -> None:
        """An empty list should return '无文字'."""
        result = extract_text_from_images([])
        assert result == "无文字"

    def test_skips_failed_images(self, text_image: Path, invalid_file: Path) -> None:
        """If one image fails OCR, processing continues with remaining."""
        result = extract_text_from_images([text_image, invalid_file, text_image])
        assert isinstance(result, str)
        # Should still have text from the valid images
        assert len(result) > 0


# =========================================================================
# Tests — OcrError
# =========================================================================


class TestOcrError:
    def test_exception_type(self) -> None:
        """OcrError is a RuntimeError subclass."""
        error = OcrError("test error")
        assert isinstance(error, RuntimeError)
        assert str(error) == "test error"

    def test_raised_on_nonexistent_path(self) -> None:
        """extract_text_from_image raises OcrError for non-existent paths."""
        with pytest.raises(OcrError):
            extract_text_from_image(Path("/nonexistent/path.png"))
