from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import tifffile
from PIL import Image


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0.0, 1.0)
    return np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(value, 1 / 2.4) - 0.055)


def make_positive(width: int, height: int) -> np.ndarray:
    y, x = np.mgrid[0:height, 0:width]
    xn = x / max(width - 1, 1)
    yn = y / max(height - 1, 1)
    sky = np.stack([
        0.22 + 0.40 * (1 - yn),
        0.34 + 0.42 * (1 - yn),
        0.52 + 0.40 * (1 - yn),
    ], axis=2)
    ground = np.stack([
        0.10 + 0.15 * xn,
        0.14 + 0.20 * xn,
        0.11 + 0.10 * xn,
    ], axis=2)
    blend = np.clip((yn - 0.48) / 0.12, 0, 1)[..., None]
    image = sky * (1 - blend) + ground * blend
    sun = np.exp(-(((xn - 0.72) / 0.11) ** 2 + ((yn - 0.28) / 0.14) ** 2))
    image += sun[..., None] * np.array([0.75, 0.62, 0.35])
    mountain = yn > (0.58 - 0.18 * np.exp(-((xn - 0.45) / 0.22) ** 2))
    image[mountain] *= np.array([0.42, 0.48, 0.45])
    river = np.abs(xn - (0.50 + 0.12 * np.sin(yn * 11))) < (0.018 + 0.06 * yn)
    image[river] = np.array([0.18, 0.58, 0.70]) * (0.7 + 0.3 * (1 - yn[river]))[:, None]
    image = np.clip(image, 0, 1).astype(np.float32)
    return image


def make_negative_scan(positive: np.ndarray, border: int | None = None) -> np.ndarray:
    height, width = positive.shape[:2]
    if border is None:
        border = max(28, int(min(height, width) * 0.095))
    base_transmission = np.array([0.86, 0.58, 0.31], dtype=np.float32)
    base_density = -np.log10(base_transmission)
    dye_density = 0.08 + positive * np.array([1.15, 1.28, 1.42], dtype=np.float32)
    density = base_density + dye_density
    transmission = np.power(10.0, -density)
    canvas = np.empty((height + 2 * border, width + 2 * border, 3), dtype=np.float32)
    canvas[:] = base_transmission
    canvas[border : border + height, border : border + width] = transmission

    # Add a readable edge code to the clear film base.
    encoded = np.clip(linear_to_srgb(canvas) * 255, 0, 255).astype(np.uint8)
    cv2.putText(
        encoded,
        "KODAK 400-3 PORTRA 400",
        (border + 12, max(20, int(border * 0.68))),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.48, border / 82.0),
        (25, 25, 25),
        2,
        cv2.LINE_AA,
    )
    scan = np.where(encoded <= 10.31475, encoded / 3294.6, np.power((encoded / 255.0 + 0.055) / 1.055, 2.4))

    # Dust and one long scratch in scanner transmission space.
    rng = np.random.default_rng(20260721)
    for _ in range(75):
        x = int(rng.integers(border + 10, border + width - 10))
        y = int(rng.integers(border + 10, border + height - 10))
        radius = int(rng.integers(1, 4))
        cv2.circle(scan, (x, y), radius, (0.01, 0.01, 0.01), -1)
    cv2.line(scan, (border + width // 3, border + 70), (border + width // 3 + 18, border + height - 80), (0.015, 0.015, 0.015), 3)
    return np.clip(scan, 0, 1).astype(np.float32)


def add_sensor_noise(image: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.0018, image.shape).astype(np.float32)
    return np.clip(image + noise, 0, 1)


def save_scan(path: Path, linear_scan: np.ndarray, exposure: float) -> None:
    exposed = np.clip(linear_scan * exposure, 0, 1)
    encoded = linear_to_srgb(add_sensor_noise(exposed, int(exposure * 10000) + 7))
    image16 = np.clip(np.round(encoded * 65535), 0, 65535).astype(np.uint16)
    tifffile.imwrite(path, image16, photometric="rgb", compression="deflate", metadata=None)


def make_ir(scan: np.ndarray) -> np.ndarray:
    gray = 0.68 + 0.04 * cv2.GaussianBlur(np.mean(scan, axis=2), (0, 0), 8)
    defect = np.mean(scan, axis=2) < 0.025
    gray[defect] = 0.02
    gray = cv2.GaussianBlur(gray.astype(np.float32), (3, 3), 0.35)
    return np.clip(gray, 0, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=640)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    positive = make_positive(args.width, args.height)
    scan = make_negative_scan(positive)
    save_scan(args.output_dir / "scan-mid.tiff", scan, 1.0)
    save_scan(args.output_dir / "scan-dark.tiff", scan, 0.55)
    save_scan(args.output_dir / "scan-bright.tiff", scan, 1.55)
    Image.fromarray(np.clip(linear_to_srgb(positive) * 255, 0, 255).astype(np.uint8), "RGB").save(args.output_dir / "positive-reference.jpg", quality=95)
    ir = make_ir(scan)
    Image.fromarray(np.clip(ir * 255, 0, 255).astype(np.uint8), "L").save(args.output_dir / "infrared.png")


if __name__ == "__main__":
    main()
