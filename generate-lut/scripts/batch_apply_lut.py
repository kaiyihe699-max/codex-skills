#!/usr/bin/env python3
"""Apply one .cube LUT to a selected image or folder without overwriting sources."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageOps
except ImportError as exc:  # pragma: no cover - exercised only when Pillow is absent.
    raise SystemExit("Pillow is required for batch image application. Install Pillow first.") from exc

from apply_lut_preview import apply_lut, parse_cube
from mix_lut_strength import read_domain


IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def collect_inputs(input_path: Path) -> tuple[list[Path], Path | None]:
    if input_path.is_file():
        if input_path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image extension: {input_path.suffix}")
        return [input_path.resolve()], None
    if not input_path.is_dir():
        raise ValueError(f"Input image or folder does not exist: {input_path}")
    paths = sorted(path.resolve() for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    if not paths:
        raise ValueError(f"No supported images found in {input_path}")
    return paths, input_path.resolve()


def plan_outputs(inputs: list[Path], source_root: Path | None, output_dir: Path) -> list[tuple[Path, Path]]:
    resolved_output = output_dir.resolve()
    if source_root is not None:
        try:
            resolved_output.relative_to(source_root)
        except ValueError:
            pass
        else:
            raise ValueError("Choose an output folder outside the input image folder.")

    plan = []
    for source in inputs:
        relative = source.relative_to(source_root) if source_root else Path(source.name)
        target = resolved_output / relative.parent / f"{relative.stem}-lut.png"
        plan.append((source, target))
    targets = [target for _, target in plan]
    if len({str(path).casefold() for path in targets}) != len(targets):
        raise ValueError("Two input files would produce the same output filename.")
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite existing output(s): " + ", ".join(existing[:5]))
    return plan


def process_image(source: Path, target: Path, size: int, values: list[tuple[float, float, float]], strength: float) -> None:
    with Image.open(source) as opened:
        if opened.mode == "CMYK":
            raise ValueError("CMYK input is not supported; convert it to RGB with a managed workflow first.")
        if opened.mode not in {"1", "L", "P", "RGB", "RGBA"}:
            raise ValueError(f"Image mode {opened.mode} is not supported by this 8-bit RGB LUT workflow.")
        info = dict(opened.info)
        oriented = ImageOps.exif_transpose(opened)
        if oriented.mode == "P" and "transparency" in info:
            oriented = oriented.convert("RGBA")
        graded = apply_lut(oriented, size, values, strength)
        target.parent.mkdir(parents=True, exist_ok=True)
        save_options: dict[str, Any] = {}
        if info.get("icc_profile") and opened.mode in {"P", "RGB", "RGBA"}:
            save_options["icc_profile"] = info["icc_profile"]
        exif = oriented.getexif()
        if exif:
            save_options["exif"] = exif.tobytes()
        graded.save(target, format="PNG", **save_options)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-apply a 3D .cube LUT to selected images or a folder.")
    parser.add_argument("--input", required=True, help="Input image or folder; folder search is recursive.")
    parser.add_argument("--lut", required=True, help="Input 3D .cube LUT.")
    parser.add_argument("--output-dir", required=True, help="New output folder; source files are never overwritten.")
    parser.add_argument("--strength", type=float, default=1.0, help="LUT amount from 0 (identity) to 1 (full effect).")
    args = parser.parse_args()
    if not 0.0 <= args.strength <= 1.0:
        parser.error("--strength must be between 0 and 1")
    return args


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    try:
        inputs, source_root = collect_inputs(input_path)
        plan = plan_outputs(inputs, source_root, output_dir)
        if (output_dir / "batch-report.json").exists():
            raise FileExistsError(f"Refusing to overwrite existing report: {output_dir / 'batch-report.json'}")
        lut_path = Path(args.lut)
        lut_size, lut_values = parse_cube(lut_path)
        domain_min, domain_max = read_domain(lut_path)
        if any(abs(value) > 1e-9 for value in domain_min) or any(abs(value - 1.0) > 1e-9 for value in domain_max):
            raise ValueError("Batch application requires DOMAIN_MIN 0 and DOMAIN_MAX 1.")
        if any(not math.isfinite(channel) or channel < 0.0 or channel > 1.0 for row in lut_values for channel in row):
            raise ValueError("Batch application requires finite LUT outputs in 0..1; no values were clipped.")
        report: dict[str, Any] = {"lut": Path(args.lut).name, "strength": args.strength, "outputs": []}
        output_dir.mkdir(parents=True, exist_ok=True)
        failures = 0
        for source, target in plan:
            try:
                process_image(source, target, lut_size, lut_values, args.strength)
                status = "OK"
            except Exception as exc:
                failures += 1
                status = f"ERROR: {exc}"
            relative_source = source.relative_to(source_root) if source_root else Path(source.name)
            report["outputs"].append({"input": str(relative_source), "output": str(target.relative_to(output_dir.resolve())), "status": status})
            print(f"{status}: {source.name}")

        report_path = output_dir / "batch-report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote report: {report_path}")
        if failures:
            sys.exit(1)
        print(f"Completed {len(plan)} image(s).")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Could not batch-apply LUT: {exc}") from exc


if __name__ == "__main__":
    main()
