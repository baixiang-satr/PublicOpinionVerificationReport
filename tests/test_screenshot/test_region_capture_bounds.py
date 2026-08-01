from pathlib import Path

from PIL import Image

from src.config.settings import TaskConfig
from src.screenshot.region_capture import _save_region


def test_save_region_clamps_to_frozen_screen_bounds(tmp_path: Path) -> None:
    source = Image.new("RGB", (800, 600), "#2f6f9f")
    output = tmp_path / "edge.jpg"

    _save_region(
        TaskConfig(),
        source,
        {"x": 760, "y": 560, "width": 200, "height": 120},
        output,
    )

    with Image.open(output) as saved:
        assert saved.size == (40, 40)
