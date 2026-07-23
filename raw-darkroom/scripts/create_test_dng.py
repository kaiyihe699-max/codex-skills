#!/usr/bin/env python3
"""Create a small deterministic Bayer DNG for smoke tests."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import tifffile


def scene_rgb(width: int, height: int) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    x = xx / max(1, width - 1)
    y = yy / max(1, height - 1)
    scene = np.zeros((height, width, 3), dtype=np.float32)
    scene[..., 0] = 0.025 + 0.72 * x
    scene[..., 1] = 0.018 + 0.58 * x + 0.10 * (1.0 - y)
    scene[..., 2] = 0.035 + 0.42 * x + 0.20 * y

    sun = ((x - 0.79) ** 2 + (y - 0.24) ** 2) < 0.055**2
    halo = np.exp(-((x - 0.79) ** 2 + (y - 0.24) ** 2) / 0.012)
    scene += halo[..., None] * np.array([0.82, 0.62, 0.34], dtype=np.float32)
    scene[sun] = np.array([1.35, 1.20, 0.88], dtype=np.float32)

    scene[(x < 0.26) & (y > 0.58)] *= 0.055
    scene[(x > 0.33) & (x < 0.48) & (y > 0.62)] *= np.array([0.50, 0.78, 1.10])

    for index, color in enumerate(
        (
            (0.78, 0.12, 0.08),
            (0.08, 0.65, 0.16),
            (0.08, 0.22, 0.78),
            (0.72, 0.58, 0.09),
        )
    ):
        left = 0.08 + index * 0.14
        patch = (x > left) & (x < left + 0.09) & (y > 0.16) & (y < 0.31)
        scene[patch] = np.array(color, dtype=np.float32)
    return np.clip(scene, 0.0, 1.4)


def mosaic_bayer(scene: np.ndarray, black: int, white: int, seed: int) -> np.ndarray:
    height, width, _ = scene.shape
    mosaic = np.zeros((height, width), dtype=np.float32)
    # BGGR, matching CFAPattern [2, 1, 1, 0].
    mosaic[0::2, 0::2] = scene[0::2, 0::2, 2]
    mosaic[0::2, 1::2] = scene[0::2, 1::2, 1]
    mosaic[1::2, 0::2] = scene[1::2, 0::2, 1]
    mosaic[1::2, 1::2] = scene[1::2, 1::2, 0]
    rng = np.random.default_rng(seed)
    shot = rng.normal(0.0, np.sqrt(np.clip(mosaic, 0, 1)) * 0.0045, mosaic.shape)
    read = rng.normal(0.0, 0.0018, mosaic.shape)
    encoded = black + (mosaic + shot + read) * (white - black)
    return np.clip(np.round(encoded), 0, white).astype(np.uint16)


def write_dng(
    path: Path,
    mosaic: np.ndarray,
    black: int,
    white: int,
    orientation: int = 1,
) -> None:
    height, width = mosaic.shape
    with tifffile.TiffWriter(path, bigtiff=False) as tif:
        tif.write(
            mosaic,
            photometric=32803,
            compression=1,
            planarconfig=1,
            bitspersample=16,
            rowsperstrip=height,
            metadata=None,
            extratags=[
                (254, "I", 1, 0, True),
                (271, "s", 0, "RAW Darkroom", True),
                (272, "s", 0, "Synthetic Bayer Test", True),
                (274, "H", 1, orientation, True),
                (50706, "B", 4, b"\x01\x04\x00\x00", True),
                (50707, "B", 4, b"\x01\x01\x00\x00", True),
                (50708, "s", 0, "RAW Darkroom Synthetic", True),
                (50709, "s", 0, "RAW Darkroom Synthetic", True),
                (50714, "H", 1, black, True),
                (50717, "H", 1, white, True),
                (50718, "I", 2, [1, 1], True),
                (50780, "I", 2, [1, 1], True),
                (
                    50721,
                    "2i",
                    9,
                    [
                        10500,
                        10000,
                        -1200,
                        10000,
                        -300,
                        10000,
                        -900,
                        10000,
                        10800,
                        10000,
                        100,
                        10000,
                        -200,
                        10000,
                        700,
                        10000,
                        9500,
                        10000,
                    ],
                    True,
                ),
                (50778, "H", 1, 21, True),
                (50728, "2I", 3, [1, 2, 1, 1, 3, 4], True),
                (50829, "I", 4, [0, 0, height, width], True),
                (33421, "H", 2, [2, 2], True),
                (33422, "B", 4, [2, 1, 1, 0], True),
                (50710, "B", 3, [0, 1, 2], True),
                (50711, "H", 1, 1, True),
            ],
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default="raw-darkroom-test.dng")
    parser.add_argument("--width", type=int, default=720)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--seed", type=int, default=37)
    parser.add_argument("--orientation", type=int, choices=(1, 3, 5, 6), default=1)
    args = parser.parse_args()
    width = max(128, args.width + args.width % 2)
    height = max(128, args.height + args.height % 2)
    black, white = 64, 4095
    scene = scene_rgb(width, height)
    mosaic = mosaic_bayer(scene, black, white, args.seed)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    write_dng(output, mosaic, black, white, args.orientation)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
