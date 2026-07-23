#!/usr/bin/env python3
"""Apply a .cube LUT to an image and create an adjusted or comparison preview."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError as exc:  # pragma: no cover - exercised only when Pillow is absent.
    raise SystemExit("Pillow is required for image previews. Install Pillow or use validate/generate only.") from exc


def parse_cube(path: Path) -> tuple[int, list[tuple[float, float, float]]]:
    size = None
    values: list[tuple[float, float, float]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            upper = line.upper()
            if upper.startswith("TITLE") or upper.startswith("DOMAIN_MIN") or upper.startswith("DOMAIN_MAX"):
                continue
            if upper.startswith("LUT_3D_SIZE"):
                parts = line.split()
                if len(parts) != 2:
                    raise ValueError(f"Invalid LUT_3D_SIZE line: {line}")
                size = int(parts[1])
                continue
            if upper.startswith("LUT_1D_SIZE"):
                raise ValueError("1D LUTs are not supported by this preview script.")
            parts = line.split()
            if len(parts) >= 3:
                try:
                    rgb = tuple(float(part) for part in parts[:3])
                except ValueError as exc:
                    raise ValueError(f"Invalid RGB row: {line}") from exc
                values.append(rgb)
    if size is None:
        raise ValueError("Missing LUT_3D_SIZE.")
    expected = size ** 3
    if len(values) != expected:
        raise ValueError(f"Expected {expected} LUT entries for size {size}, found {len(values)}.")
    return size, values


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def lerp(a: float, b: float, amount: float) -> float:
    return a + (b - a) * amount


def lookup(values: list[tuple[float, float, float]], size: int, r: float, g: float, b: float) -> tuple[float, float, float]:
    scale = size - 1
    rf, gf, bf = clamp(r) * scale, clamp(g) * scale, clamp(b) * scale
    r0, g0, b0 = int(math.floor(rf)), int(math.floor(gf)), int(math.floor(bf))
    r1, g1, b1 = min(r0 + 1, scale), min(g0 + 1, scale), min(b0 + 1, scale)
    tr, tg, tb = rf - r0, gf - g0, bf - b0

    def at(ri: int, gi: int, bi: int) -> tuple[float, float, float]:
        return values[((bi * size) + gi) * size + ri]

    c000 = at(r0, g0, b0)
    c100 = at(r1, g0, b0)
    c010 = at(r0, g1, b0)
    c110 = at(r1, g1, b0)
    c001 = at(r0, g0, b1)
    c101 = at(r1, g0, b1)
    c011 = at(r0, g1, b1)
    c111 = at(r1, g1, b1)

    out = []
    for channel in range(3):
        c00 = lerp(c000[channel], c100[channel], tr)
        c10 = lerp(c010[channel], c110[channel], tr)
        c01 = lerp(c001[channel], c101[channel], tr)
        c11 = lerp(c011[channel], c111[channel], tr)
        c0 = lerp(c00, c10, tg)
        c1 = lerp(c01, c11, tg)
        out.append(clamp(lerp(c0, c1, tb)))
    return tuple(out)


def resize_for_preview(image: Image.Image, max_size: int) -> Image.Image:
    if max(image.size) <= max_size:
        return image
    copy = image.copy()
    copy.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return copy


def apply_lut(image: Image.Image, size: int, values: list[tuple[float, float, float]]) -> Image.Image:
    has_alpha = image.mode == "RGBA"
    source = image.convert("RGBA" if has_alpha else "RGB")
    output = Image.new(source.mode, source.size)
    result = []
    pixel_data = source.get_flattened_data() if hasattr(source, "get_flattened_data") else source.getdata()
    for pixel in pixel_data:
        r, g, b = pixel[:3]
        rr, gg, bb = lookup(values, size, r / 255.0, g / 255.0, b / 255.0)
        mapped = (round(rr * 255), round(gg * 255), round(bb * 255))
        if has_alpha:
            mapped = mapped + (pixel[3],)
        result.append(mapped)
    output.putdata(result)
    return output


def comparison(before: Image.Image, after: Image.Image) -> Image.Image:
    before_rgb = before.convert("RGB")
    after_rgb = after.convert("RGB")
    canvas = Image.new("RGB", (before_rgb.width * 2, before_rgb.height), "white")
    canvas.paste(before_rgb, (0, 0))
    canvas.paste(after_rgb, (before_rgb.width, 0))
    return canvas


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a .cube 3D LUT to an image preview.")
    parser.add_argument("--input", required=True, help="Input image path.")
    parser.add_argument("--lut", required=True, help=".cube LUT path.")
    parser.add_argument("--output", required=True, help="Output image path.")
    parser.add_argument("--mode", choices=("adjusted", "comparison"), default="comparison", help="Preview output mode.")
    parser.add_argument("--max-size", type=int, default=1400, help="Resize longest edge before previewing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lut_size, values = parse_cube(Path(args.lut))
    image = ImageOps.exif_transpose(Image.open(args.input))
    image = resize_for_preview(image, args.max_size)
    adjusted = apply_lut(image, lut_size, values)
    output = comparison(image, adjusted) if args.mode == "comparison" else adjusted
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
