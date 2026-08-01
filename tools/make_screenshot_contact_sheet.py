"""Create compact contact sheets for visual QA of captured evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def build(input_dir: Path, output_dir: Path, *, per_sheet: int = 6) -> list[Path]:
    files = sorted(
        path
        for path in input_dir.iterdir()
        if path.suffix.casefold() in {".jpg", ".jpeg", ".png"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    thumb_size = (680, 425)
    cell_size = (700, 465)
    for page_no, start in enumerate(range(0, len(files), per_sheet), start=1):
        batch = files[start : start + per_sheet]
        rows = (len(batch) + 1) // 2
        canvas = Image.new("RGB", (cell_size[0] * 2, cell_size[1] * rows), "white")
        draw = ImageDraw.Draw(canvas)
        for index, path in enumerate(batch):
            with Image.open(path) as source:
                preview = ImageOps.contain(source.convert("RGB"), thumb_size)
            left = (index % 2) * cell_size[0] + (cell_size[0] - preview.width) // 2
            top = (index // 2) * cell_size[1] + 30
            canvas.paste(preview, (left, top))
            draw.text(((index % 2) * cell_size[0] + 10, (index // 2) * cell_size[1] + 8), path.name, fill="black")
        output = output_dir / f"contact-{page_no}.jpg"
        canvas.save(output, quality=90)
        outputs.append(output)
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    for result in build(args.input_dir, args.output_dir):
        print(result)
