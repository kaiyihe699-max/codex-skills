#!/usr/bin/env python3
"""Validate a .cube 3D LUT file and report practical quality warnings."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def parse_cube(path: Path) -> tuple[int, list[tuple[float, float, float]], list[str]]:
    size = None
    values: list[tuple[float, float, float]] = []
    notes: list[str] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            upper = line.upper()
            if upper.startswith("TITLE") or upper.startswith("DOMAIN_MIN") or upper.startswith("DOMAIN_MAX"):
                continue
            if upper.startswith("LUT_3D_SIZE"):
                parts = line.split()
                if len(parts) != 2:
                    raise ValueError(f"Line {line_number}: invalid LUT_3D_SIZE.")
                size = int(parts[1])
                continue
            if upper.startswith("LUT_1D_SIZE"):
                raise ValueError("1D LUTs are not supported by this validator.")
            parts = line.split()
            if len(parts) < 3:
                notes.append(f"Line {line_number}: ignored non-RGB row.")
                continue
            try:
                rgb = tuple(float(part) for part in parts[:3])
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: invalid numeric RGB row.") from exc
            values.append(rgb)
    if size is None:
        raise ValueError("Missing LUT_3D_SIZE.")
    if size < 2:
        raise ValueError("LUT_3D_SIZE must be at least 2.")
    expected = size ** 3
    if len(values) != expected:
        raise ValueError(f"Expected {expected} entries for size {size}, found {len(values)}.")
    return size, values, notes


def luminance(rgb: tuple[float, float, float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def entry(values: list[tuple[float, float, float]], size: int, r: int, g: int, b: int) -> tuple[float, float, float]:
    return values[((b * size) + g) * size + r]


def analyze(size: int, values: list[tuple[float, float, float]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    finite_failures = 0
    range_failures = 0
    interior_clips = 0
    interior_entries = 0

    for index, rgb in enumerate(values):
        b = index // (size * size)
        rem = index % (size * size)
        g = rem // size
        r = rem % size
        interior = 0 < r < size - 1 and 0 < g < size - 1 and 0 < b < size - 1
        if interior:
            interior_entries += 1
        for channel in rgb:
            if not math.isfinite(channel):
                finite_failures += 1
            if channel < -0.00001 or channel > 1.00001:
                range_failures += 1
            if interior and (channel <= 0.0005 or channel >= 0.9995):
                interior_clips += 1

    if finite_failures:
        errors.append(f"{finite_failures} channel values are not finite.")
    if range_failures:
        errors.append(f"{range_failures} channel values are outside 0..1.")

    if interior_entries:
        clip_rate = interior_clips / (interior_entries * 3)
        if clip_rate > 0.01:
            warnings.append(f"Interior clipping is {clip_rate:.2%}; this may crush detail.")

    neutral_drifts = []
    for i in range(size):
        rgb = entry(values, size, i, i, i)
        neutral_drifts.append(max(rgb) - min(rgb))
    max_drift = max(neutral_drifts)
    avg_drift = sum(neutral_drifts) / len(neutral_drifts)
    if max_drift > 0.10:
        warnings.append(f"Neutral grey max channel drift is {max_drift:.4f}; confirm this tint is intentional.")
    elif avg_drift > 0.035:
        warnings.append(f"Neutral grey average channel drift is {avg_drift:.4f}; check for unwanted color cast.")

    monotonic_drops = 0
    checks = 0
    for b in range(size):
        for g in range(size):
            previous = luminance(entry(values, size, 0, g, b))
            for r in range(1, size):
                current = luminance(entry(values, size, r, g, b))
                checks += 1
                if current + 0.030 < previous:
                    monotonic_drops += 1
                previous = current
    for b in range(size):
        for r in range(size):
            previous = luminance(entry(values, size, r, 0, b))
            for g in range(1, size):
                current = luminance(entry(values, size, r, g, b))
                checks += 1
                if current + 0.030 < previous:
                    monotonic_drops += 1
                previous = current
    for g in range(size):
        for r in range(size):
            previous = luminance(entry(values, size, r, g, 0))
            for b in range(1, size):
                current = luminance(entry(values, size, r, g, b))
                checks += 1
                if current + 0.030 < previous:
                    monotonic_drops += 1
                previous = current
    if checks and monotonic_drops / checks > 0.005:
        warnings.append(f"Detected {monotonic_drops} luminance reversals; inspect for banding or hue artifacts.")

    return errors, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a .cube 3D LUT.")
    parser.add_argument("lut", help="Path to a .cube LUT.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.lut)
    try:
        size, values, notes = parse_cube(path)
        errors, warnings = analyze(size, values)
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from exc

    print(f"LUT: {path}")
    print(f"Size: {size} ({len(values)} entries)")
    for note in notes:
        print(f"NOTE: {note}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")

    if errors or (args.strict and warnings):
        raise SystemExit(1)
    print("Validation passed")


if __name__ == "__main__":
    main()
