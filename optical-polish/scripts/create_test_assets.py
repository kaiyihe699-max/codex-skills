#!/usr/bin/env python3
"""Create deterministic optical-alignment samples for smoke testing."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def make_triangle(path: Path) -> None:
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon([(31, 26), (31, 70), (70, 48)], fill=(245, 247, 250, 255))
    image.save(path)


def make_circle(path: Path) -> None:
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((27, 27, 68, 68), fill=(245, 247, 250, 255))
    image.save(path)


def make_button(path: Path) -> None:
    width, height = 260, 80
    image = Image.new("RGBA", (width, height), (18, 24, 34, 255))
    draw = ImageDraw.Draw(image)
    typeface = font(28)
    label = "Play"
    bbox = draw.textbbox((0, 0), label, font=typeface)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    icon_w = 22
    gap = 12
    group_w = icon_w + gap + text_w
    intended_center = width / 2 - 7
    start_x = int(round(intended_center - group_w / 2))
    draw.polygon(
        [(start_x, 27), (start_x, 53), (start_x + icon_w, 40)],
        fill=(244, 247, 251, 255),
    )
    text_x = start_x + icon_w + gap
    text_y = int(round((height - text_h) / 2 - bbox[1]))
    draw.text((text_x, text_y), label, font=typeface, fill=(244, 247, 251, 255))
    image.save(path)


def make_button_canvas(button_path: Path, output_path: Path) -> None:
    canvas = Image.new("RGBA", (400, 180), (229, 234, 241, 255))
    button = Image.open(button_path).convert("RGBA")
    canvas.alpha_composite(button, (70, 50))
    canvas.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="optical-polish-test-assets")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    make_triangle(output / "directional-triangle.png")
    make_circle(output / "balanced-circle.png")
    button_path = output / "shifted-play-button.png"
    make_button(button_path)
    make_button_canvas(button_path, output / "button-in-canvas.png")
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
