#!/usr/bin/env python3
"""Measure optical balance and render an evidence-based correction preview."""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


SUPPORTED_RASTER = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class Analysis:
    background: np.ndarray
    background_dominance: float
    transparent_mode: bool
    distance: np.ndarray
    weights: np.ndarray
    mask: np.ndarray
    soft_mask: np.ndarray
    threshold: float
    bbox: tuple[float, float, float, float]
    visual_centroid: tuple[float, float]
    bbox_center: tuple[float, float]
    target_center: tuple[float, float]
    centroid_correction: tuple[float, float]
    whitespace_correction: tuple[float, float]
    recommended_float: tuple[float, float]
    recommended_shift: tuple[int, int]
    confidence_score: float
    confidence: str
    foreground_fraction: float
    mean_contrast: float
    border: dict[str, Any] | None


def parse_hex_color(value: str) -> np.ndarray:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) != 6:
        raise argparse.ArgumentTypeError("background must be #RGB or #RRGGBB")
    try:
        return np.array([int(text[index:index + 2], 16) for index in (0, 2, 4)], dtype=np.float32)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("background must be #RGB or #RRGGBB") from exc


def parse_container(value: str) -> tuple[int, int, int, int]:
    try:
        parts = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("container must be x,y,w,h") from exc
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise argparse.ArgumentTypeError("container must be x,y,w,h with positive width and height")
    return parts


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    value = rgb.astype(np.float32) / 255.0
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def estimate_background(rgb: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, float]:
    opaque = alpha > 0.5
    samples = rgb[opaque]
    if samples.size == 0:
        return np.array([255.0, 255.0, 255.0], dtype=np.float32), 0.0
    quantized = np.clip((samples // 16).astype(np.int32), 0, 15)
    keys = quantized[:, 0] * 256 + quantized[:, 1] * 16 + quantized[:, 2]
    counts = np.bincount(keys, minlength=4096)
    dominant_key = int(np.argmax(counts))
    cluster = samples[keys == dominant_key]
    background = np.median(cluster, axis=0).astype(np.float32)
    dominance = float(cluster.shape[0] / max(1, samples.shape[0]))
    return background, dominance


def otsu_threshold(values: np.ndarray) -> float:
    clipped = np.clip(values[np.isfinite(values)], 0.0, 1.0)
    if clipped.size == 0 or float(np.max(clipped)) <= 1e-6:
        return 0.035
    hist, edges = np.histogram(clipped, bins=256, range=(0.0, 1.0))
    probability = hist.astype(np.float64) / max(1, hist.sum())
    centers = (edges[:-1] + edges[1:]) * 0.5
    cumulative = np.cumsum(probability)
    mean = np.cumsum(probability * centers)
    global_mean = mean[-1]
    denominator = cumulative * (1.0 - cumulative)
    score = np.zeros_like(denominator)
    valid = denominator > 1e-12
    score[valid] = ((global_mean * cumulative[valid] - mean[valid]) ** 2) / denominator[valid]
    return float(centers[int(np.argmax(score))])


def weighted_quantile_axis(axis_weights: np.ndarray, quantile: float) -> float:
    total = float(axis_weights.sum())
    if total <= 0:
        return 0.0
    cumulative = np.cumsum(axis_weights)
    return float(np.searchsorted(cumulative, total * quantile, side="left"))


def exclusion_ring(height: int, width: int, border_width: int) -> np.ndarray:
    ring = np.zeros((height, width), dtype=bool)
    if border_width <= 0:
        return ring
    thickness = min(border_width, max(1, min(height, width) // 4))
    ring[:thickness, :] = True
    ring[-thickness:, :] = True
    ring[:, :thickness] = True
    ring[:, -thickness:] = True
    return ring


def confidence_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def analyze_crop(
    crop: Image.Image,
    background_override: np.ndarray | None,
    border_width: int,
    max_shift: int,
) -> Analysis:
    rgba = np.asarray(crop.convert("RGBA"), dtype=np.float32)
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3] / 255.0
    height, width = alpha.shape
    opaque_fraction = float(np.mean(alpha > 0.95))
    transparent_mode = opaque_fraction < 0.65 and float(np.mean(alpha < 0.05)) > 0.1

    estimated_background, dominance = estimate_background(rgb, alpha)
    background = background_override if background_override is not None else estimated_background
    background_linear = srgb_to_linear(background.reshape(1, 1, 3))[0, 0]
    rgb_linear = srgb_to_linear(rgb)
    color_distance = np.sqrt(np.mean((rgb_linear - background_linear) ** 2, axis=2))

    valid = alpha > 0.02
    if transparent_mode:
        distance = alpha.copy()
        threshold = 0.03
        weights = np.power(np.clip(alpha, 0.0, 1.0), 1.15)
        soft_mask = np.clip(alpha, 0.0, 1.0)
        dominance = max(dominance, 0.9)
    else:
        distance = color_distance * alpha
        threshold = max(0.025, otsu_threshold(distance[valid]) * 0.55)
        raw = np.clip(distance - threshold * 0.35, 0.0, None)
        weights = np.power(raw, 1.25) * alpha
        maximum = float(np.max(distance))
        soft_mask = np.clip(distance / max(maximum, threshold, 1e-6), 0.0, 1.0) * alpha

    ring = exclusion_ring(height, width, border_width)
    weights[ring] = 0.0
    soft_mask[ring] = 0.0
    mask = (distance > threshold) & valid & ~ring

    total = float(weights.sum())
    if total <= 1e-8 or not np.any(mask):
        raise ValueError("No measurable foreground was found. Crop the component more tightly or set --background.")

    y_coords, x_coords = np.indices((height, width), dtype=np.float32)
    visual_x = float(np.sum(weights * x_coords) / total)
    visual_y = float(np.sum(weights * y_coords) / total)

    axis_x = weights.sum(axis=0)
    axis_y = weights.sum(axis=1)
    left = weighted_quantile_axis(axis_x, 0.005)
    right = weighted_quantile_axis(axis_x, 0.995)
    top = weighted_quantile_axis(axis_y, 0.005)
    bottom = weighted_quantile_axis(axis_y, 0.995)
    bbox_center = ((left + right) / 2.0, (top + bottom) / 2.0)
    target = ((width - 1) / 2.0, (height - 1) / 2.0)

    centroid_correction = (target[0] - visual_x, target[1] - visual_y)
    whitespace_correction = (target[0] - bbox_center[0], target[1] - bbox_center[1])
    recommended_float = (
        0.65 * centroid_correction[0] + 0.35 * whitespace_correction[0],
        0.65 * centroid_correction[1] + 0.35 * whitespace_correction[1],
    )
    recommended_shift = tuple(
        0 if abs(value) < 0.75 else int(np.clip(round(value), -max_shift, max_shift))
        for value in recommended_float
    )

    foreground_fraction = float(np.mean(mask))
    contrast_values = distance[mask]
    mean_contrast = float(np.mean(contrast_values)) if contrast_values.size else 0.0
    agreement_distance = math.hypot(
        centroid_correction[0] - whitespace_correction[0],
        centroid_correction[1] - whitespace_correction[1],
    )
    agreement = math.exp(-agreement_distance / max(2.0, min(width, height) * 0.08))
    fraction_score = 1.0 if 0.008 <= foreground_fraction <= 0.42 else 0.45
    contrast_score = min(1.0, mean_contrast / 0.18)
    background_score = 1.0 if transparent_mode else min(1.0, dominance / 0.38)
    confidence_score = float(np.clip(
        0.32 * background_score + 0.28 * agreement + 0.22 * contrast_score + 0.18 * fraction_score,
        0.0,
        1.0,
    ))

    border = None
    if border_width > 0:
        ring_values = color_distance[ring & valid]
        border_contrast = float(np.mean(ring_values)) if ring_values.size else 0.0
        border = {
            "width_px": border_width,
            "mean_contrast": round(border_contrast, 4),
            "apparent_border_weight": round(border_width * border_contrast, 4),
        }

    return Analysis(
        background=background,
        background_dominance=dominance,
        transparent_mode=transparent_mode,
        distance=distance,
        weights=weights,
        mask=mask,
        soft_mask=soft_mask,
        threshold=threshold,
        bbox=(left, top, right, bottom),
        visual_centroid=(visual_x, visual_y),
        bbox_center=bbox_center,
        target_center=target,
        centroid_correction=centroid_correction,
        whitespace_correction=whitespace_correction,
        recommended_float=recommended_float,
        recommended_shift=(int(recommended_shift[0]), int(recommended_shift[1])),
        confidence_score=confidence_score,
        confidence=confidence_label(confidence_score),
        foreground_fraction=foreground_fraction,
        mean_contrast=mean_contrast,
        border=border,
    )


def load_input(path: Path, svg_width: int | None) -> Image.Image:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_RASTER:
        return Image.open(path).convert("RGBA")
    if suffix == ".svg":
        try:
            import cairosvg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("SVG input requires CairoSVG. Install requirements.txt or export the SVG as PNG.") from exc
        png = cairosvg.svg2png(url=str(path), output_width=svg_width)
        return Image.open(BytesIO(png)).convert("RGBA")
    raise ValueError(f"Unsupported input type: {suffix or '(none)'}")


def shift_crop(crop: Image.Image, analysis: Analysis) -> Image.Image:
    rgba = np.asarray(crop.convert("RGBA"), dtype=np.float32)
    height, width = analysis.soft_mask.shape
    soft = analysis.soft_mask[:, :, None]
    dx, dy = analysis.recommended_shift

    if analysis.transparent_mode:
        base = np.zeros_like(rgba)
        source = rgba.copy()
    else:
        background = np.zeros_like(rgba)
        background[:, :, :3] = analysis.background.reshape(1, 1, 3)
        background[:, :, 3] = 255.0
        removal = analysis.soft_mask > 0.015
        padded = np.pad(removal, 1, mode="constant")
        expanded = np.zeros_like(removal)
        for row in range(3):
            for column in range(3):
                expanded |= padded[row:row + height, column:column + width]
        base = rgba.copy()
        base[expanded] = background[expanded]

        coverage = np.clip(soft, 0.02, 1.0)
        recovered_rgb = (
            rgba[:, :, :3] - (1.0 - coverage) * analysis.background.reshape(1, 1, 3)
        ) / coverage
        source = np.zeros_like(rgba)
        source[:, :, :3] = np.clip(recovered_rgb, 0.0, 255.0)
        source[:, :, 3:4] = np.clip(soft * 255.0, 0.0, 255.0)
    layer = np.zeros_like(rgba)

    src_x0 = max(0, -dx)
    src_y0 = max(0, -dy)
    src_x1 = min(width, width - dx)
    src_y1 = min(height, height - dy)
    dst_x0 = max(0, dx)
    dst_y0 = max(0, dy)
    dst_x1 = dst_x0 + max(0, src_x1 - src_x0)
    dst_y1 = dst_y0 + max(0, src_y1 - src_y0)
    if src_x1 > src_x0 and src_y1 > src_y0:
        layer[dst_y0:dst_y1, dst_x0:dst_x1] = source[src_y0:src_y1, src_x0:src_x1]

    layer_alpha = layer[:, :, 3:4] / 255.0
    base_rgb = base[:, :, :3]
    base_alpha = base[:, :, 3:4] / 255.0
    out_alpha = layer_alpha + base_alpha * (1.0 - layer_alpha)
    numerator = layer[:, :, :3] * layer_alpha + base_rgb * base_alpha * (1.0 - layer_alpha)
    out_rgb = np.zeros_like(numerator)
    np.divide(
        numerator,
        np.maximum(out_alpha, 1e-6),
        out=out_rgb,
        where=out_alpha > 0,
    )
    output = np.concatenate((out_rgb, out_alpha * 255.0), axis=2)
    return Image.fromarray(np.clip(output, 0, 255).astype(np.uint8), mode="RGBA")


def default_font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def annotate(image: Image.Image, container: tuple[int, int, int, int], analysis: Analysis) -> Image.Image:
    annotated = image.convert("RGBA").copy()
    draw = ImageDraw.Draw(annotated, "RGBA")
    x, y, width, height = container
    scale = max(1, int(round(min(image.size) / 480)))
    cyan = (94, 230, 255, 255)
    orange = (255, 159, 67, 255)
    green = (111, 231, 167, 255)
    draw.rectangle((x, y, x + width - 1, y + height - 1), outline=cyan, width=scale)
    left, top, right, bottom = analysis.bbox
    draw.rectangle((x + left, y + top, x + right, y + bottom), outline=green, width=scale)
    target_x = x + analysis.target_center[0]
    target_y = y + analysis.target_center[1]
    visual_x = x + analysis.visual_centroid[0]
    visual_y = y + analysis.visual_centroid[1]
    arm = 9 * scale
    draw.line((target_x - arm, target_y, target_x + arm, target_y), fill=cyan, width=scale)
    draw.line((target_x, target_y - arm, target_x, target_y + arm), fill=cyan, width=scale)
    radius = 4 * scale
    draw.ellipse((visual_x - radius, visual_y - radius, visual_x + radius, visual_y + radius), fill=orange)
    draw.line((visual_x, visual_y, target_x, target_y), fill=orange, width=max(1, scale))
    font = default_font(12 * scale)
    label = f"shift {analysis.recommended_shift[0]:+d}, {analysis.recommended_shift[1]:+d}px"
    label_box = draw.textbbox((0, 0), label, font=font)
    label_w = label_box[2] - label_box[0] + 12 * scale
    label_h = label_box[3] - label_box[1] + 8 * scale
    label_x = min(max(0, x), max(0, image.width - label_w))
    label_y = max(0, y - label_h - 4 * scale)
    draw.rectangle((label_x, label_y, label_x + label_w, label_y + label_h), fill=(5, 8, 12, 220))
    draw.text((label_x + 6 * scale, label_y + 3 * scale), label, fill=(255, 255, 255, 255), font=font)
    return annotated


def make_comparison(original: Image.Image, corrected: Image.Image) -> Image.Image:
    width = original.width + corrected.width
    header = 36
    canvas = Image.new("RGBA", (width, max(original.height, corrected.height) + header), (10, 13, 18, 255))
    canvas.alpha_composite(original.convert("RGBA"), (0, header))
    canvas.alpha_composite(corrected.convert("RGBA"), (original.width, header))
    draw = ImageDraw.Draw(canvas)
    font = default_font(16)
    draw.text((12, 8), "ORIGINAL", fill=(174, 183, 197, 255), font=font)
    draw.text((original.width + 12, 8), "PREVIEW", fill=(94, 230, 255, 255), font=font)
    draw.line((original.width, 0, original.width, canvas.height), fill=(43, 52, 66, 255), width=1)
    return canvas


def image_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def direction_text(dx: int, dy: int) -> str:
    pieces = []
    if dx:
        pieces.append(f"向{'右' if dx > 0 else '左'} {abs(dx)} px")
    if dy:
        pieces.append(f"向{'下' if dy > 0 else '上'} {abs(dy)} px")
    return "、".join(pieces) if pieces else "无需整数像素位移"


def result_payload(
    input_path: Path,
    container: tuple[int, int, int, int],
    analysis: Analysis,
) -> dict[str, Any]:
    dx, dy = analysis.recommended_shift
    magnitude = math.hypot(*analysis.recommended_float)
    if magnitude < 0.75:
        severity = "balanced"
    elif magnitude < 1.5:
        severity = "subtle"
    elif magnitude < 3.0:
        severity = "visible"
    else:
        severity = "strong"
    return {
        "schema_version": 1,
        "input": str(input_path.resolve()),
        "container": {"x": container[0], "y": container[1], "width": container[2], "height": container[3]},
        "measurement": {
            "background_rgb": [int(round(value)) for value in analysis.background.tolist()],
            "background_dominance": round(analysis.background_dominance, 4),
            "transparent_mode": analysis.transparent_mode,
            "threshold": round(analysis.threshold, 5),
            "foreground_fraction": round(analysis.foreground_fraction, 4),
            "mean_contrast": round(analysis.mean_contrast, 4),
            "visible_bbox": [round(value, 3) for value in analysis.bbox],
            "geometric_center": [round(value, 3) for value in analysis.target_center],
            "visual_centroid": [round(value, 3) for value in analysis.visual_centroid],
            "visible_bbox_center": [round(value, 3) for value in analysis.bbox_center],
            "centroid_correction": [round(value, 3) for value in analysis.centroid_correction],
            "whitespace_correction": [round(value, 3) for value in analysis.whitespace_correction],
            "apparent_border": analysis.border,
        },
        "assessment": {
            "severity": severity,
            "confidence": analysis.confidence,
            "confidence_score": round(analysis.confidence_score, 4),
            "recommended_shift_float": [round(value, 3) for value in analysis.recommended_float],
            "recommended_shift_px": [dx, dy],
            "summary_zh": direction_text(dx, dy),
            "css_preview": f"transform: translate({dx}px, {dy}px);",
            "warning_zh": "低置信度，仅用于人工复核。" if analysis.confidence == "low" else None,
        },
    }


def generate_report(output_dir: Path, payload: dict[str, Any]) -> None:
    script_dir = Path(__file__).resolve().parent
    css_path = script_dir.parent / "assets" / "report-style.css"
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else "body{font-family:sans-serif}"
    assessment = payload["assessment"]
    measurement = payload["measurement"]
    shift = assessment["recommended_shift_px"]
    border = measurement["apparent_border"]
    border_row = ""
    if border:
        border_row = (
            "<tr><th>边框视觉重量</th><td>"
            f"{html.escape(str(border['apparent_border_weight']))}</td></tr>"
        )
    warning = assessment.get("warning_zh")
    warning_html = f'<p class="note">{html.escape(warning)}</p>' if warning else ""
    document = f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Optical Polish Report</title><style>{css}</style></head>
<body><main>
  <h1>Optical Polish 视觉精修报告</h1>
  <p class="lede">测量视觉重心、可见边界与负空间，并生成可复核的修正预览。</p>
  <section class="grid">
    <article class="card"><div class="label">建议位移</div><div class="metric">{shift[0]:+d}, {shift[1]:+d} px</div><p>{html.escape(assessment['summary_zh'])}</p></article>
    <article class="card"><div class="label">置信度</div><div class="metric {assessment['confidence']}">{html.escape(assessment['confidence'].upper())}</div><p>{assessment['confidence_score']:.0%}</p></article>
  </section>
  {warning_html}
  <section class="card" style="margin-top:16px"><h2>原版 / 修正预览</h2><img class="visual" src="{image_data_uri(output_dir / 'comparison.png')}" alt="before and after"></section>
  <section class="grid" style="margin-top:16px">
    <article class="card"><h2>测量标注</h2><img class="visual" src="{image_data_uri(output_dir / 'annotated.png')}" alt="measurement overlay"></article>
    <article class="card"><h2>前景遮罩</h2><img class="visual" src="{image_data_uri(output_dir / 'mask.png')}" alt="foreground mask"><p>先检查遮罩是否只覆盖真正的图标与文字。</p></article>
  </section>
  <section class="card" style="margin-top:16px"><h2>测量数据</h2><table>
    <tr><th>视觉重心</th><td>{measurement['visual_centroid']}</td></tr>
    <tr><th>容器中心</th><td>{measurement['geometric_center']}</td></tr>
    <tr><th>可见边界中心</th><td>{measurement['visible_bbox_center']}</td></tr>
    <tr><th>前景占比</th><td>{measurement['foreground_fraction']:.2%}</td></tr>
    <tr><th>平均反差</th><td>{measurement['mean_contrast']}</td></tr>
    {border_row}
  </table><p>CSS 预览：<code>{html.escape(assessment['css_preview'])}</code></p></section>
  <p class="note">修正版是平面图模拟结果。品牌符号、有意不对称构图及低置信度结果必须由设计师复核。</p>
</main></body></html>"""
    (output_dir / "report.html").write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("optical-polish-output"))
    parser.add_argument("--container", type=parse_container, help="x,y,w,h; defaults to the full image")
    parser.add_argument("--background", type=parse_hex_color, help="#RGB or #RRGGBB inside the component")
    parser.add_argument("--border-width", type=int, default=0, help="known border width in pixels")
    parser.add_argument("--max-shift", type=int, default=8, help="maximum preview translation per axis")
    parser.add_argument("--svg-width", type=int, help="optional SVG rasterization width")
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"input does not exist: {args.input}")
    if args.border_width < 0:
        parser.error("--border-width must be zero or positive")
    if args.max_shift < 1:
        parser.error("--max-shift must be positive")

    try:
        image = load_input(args.input, args.svg_width)
        container = args.container or (0, 0, image.width, image.height)
        x, y, width, height = container
        if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
            raise ValueError("container is outside the image bounds")
        crop = image.crop((x, y, x + width, y + height))
        analysis = analyze_crop(crop, args.background, args.border_width, args.max_shift)
    except Exception as exc:
        print(f"Optical Polish failed: {exc}", file=sys.stderr)
        return 2

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    corrected_crop = shift_crop(crop, analysis)
    corrected = image.convert("RGBA").copy()
    corrected.paste(corrected_crop, (x, y))
    annotated = annotate(image, container, analysis)
    comparison = make_comparison(image, corrected)
    mask = Image.fromarray((analysis.soft_mask * 255.0).astype(np.uint8), mode="L")

    annotated.save(output_dir / "annotated.png")
    corrected.save(output_dir / "corrected.png")
    comparison.save(output_dir / "comparison.png")
    mask.save(output_dir / "mask.png")
    payload = result_payload(args.input, container, analysis)
    (output_dir / "analysis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    generate_report(output_dir, payload)
    print(json.dumps({
        "output_dir": str(output_dir.resolve()),
        "recommended_shift_px": payload["assessment"]["recommended_shift_px"],
        "confidence": payload["assessment"]["confidence"],
        "report": str((output_dir / "report.html").resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
