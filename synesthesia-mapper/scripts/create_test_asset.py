#!/usr/bin/env python3
"""Create a deterministic image for smoke-testing the mapper."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default="synesthesia-test.png")
    parser.add_argument("--size", type=int, default=900)
    args = parser.parse_args()

    size = max(320, args.size)
    image = Image.new("RGB", (size, size), "#07131d")
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            nx = x / max(1, size - 1)
            ny = y / max(1, size - 1)
            glow = math.exp(-((nx - 0.68) ** 2 + (ny - 0.35) ** 2) / 0.055)
            wave = 0.5 + 0.5 * math.sin(nx * 13.0 + ny * 7.0)
            r = int(8 + 185 * glow + 28 * wave * nx)
            g = int(18 + 72 * (1.0 - ny) + 72 * glow)
            b = int(29 + 145 * nx + 25 * wave)
            pixels[x, y] = tuple(max(0, min(255, value)) for value in (r, g, b))

    draw = ImageDraw.Draw(image, "RGBA")
    for i in range(11):
        radius = int(size * (0.04 + i * 0.018))
        cx = int(size * (0.12 + i * 0.072))
        cy = int(size * (0.74 - 0.12 * math.sin(i * 0.8)))
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(244, 174, 88, 70))
    draw.rectangle((0, int(size * 0.84), size, size), fill=(4, 8, 13, 150))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
