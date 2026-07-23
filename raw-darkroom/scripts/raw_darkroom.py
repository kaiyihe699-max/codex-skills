#!/usr/bin/env python3
"""Decode, analyze, develop, and report on camera RAW files."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import rawpy
import tifffile
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
RECIPES_PATH = SKILL_DIR / "assets" / "development-recipes.json"
ICC_PROFILE_PATH = SKILL_DIR / "assets" / "srgb-display-profile.b64"
PROCESSED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".avif"}
SENSITIVE_METADATA_KEYS = {
    "cameraserialnumber",
    "directory",
    "gps",
    "internalserialnumber",
    "location",
    "ownername",
    "serialnumber",
    "sourcefile",
}
BASE_OUTPUT_FILES = {
    "metadata.json",
    "analysis.json",
    "development-parameters.json",
    "sensor-preview.jpg",
    "highlight-map.png",
    "shadow-map.png",
    "contact-sheet.jpg",
    "report.html",
    "validation.json",
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def load_srgb_icc_profile() -> bytes:
    profile = base64.b64decode(ICC_PROFILE_PATH.read_text(encoding="ascii"))
    if len(profile) < 128 or profile[36:40] != b"acsp":
        raise ValueError("内置 sRGB ICC 配置无效")
    return profile


SRGB_ICC_PROFILE = load_srgb_icc_profile()


def json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return list(value)
    if hasattr(value, "_asdict"):
        return {key: json_safe(item) for key, item in value._asdict().items()}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def metadata_key_is_sensitive(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return any(token in normalized for token in SENSITIVE_METADATA_KEYS)


def redact_private_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if metadata_key_is_sensitive(str(key)) else redact_private_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_private_metadata(item) for item in value]
    return value


def orient_array(array: np.ndarray, flip: int) -> np.ndarray:
    if flip in (0, 1):
        oriented = array
    elif flip == 3:
        oriented = np.rot90(array, 2)
    elif flip == 5:
        oriented = np.rot90(array, 1)
    elif flip == 6:
        oriented = np.rot90(array, 3)
    else:
        raise ValueError(f"暂不支持 LibRaw flip={flip} 的方向变换")
    return np.ascontiguousarray(oriented)


def cfa_safe_stride(stride: int, pattern: Any) -> int:
    if pattern is None:
        return max(1, stride)
    shape = np.asarray(pattern).shape
    period = 1
    for dimension in shape[:2]:
        period = math.lcm(period, int(dimension))
    safe_stride = max(1, stride)
    while period > 1 and math.gcd(safe_stride, period) != 1:
        safe_stride += 1
    return safe_stride


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_wb(values: Any) -> bool:
    if values is None:
        return False
    array = np.asarray(values, dtype=np.float64)
    return array.size >= 3 and np.all(np.isfinite(array[:3])) and np.all(array[:3] > 0)


def white_balance_kwargs(raw: rawpy.RawPy, requested: str) -> tuple[dict[str, Any], str]:
    if requested == "camera" and valid_wb(raw.camera_whitebalance):
        return {"use_camera_wb": True, "use_auto_wb": False}, "camera"
    if requested == "daylight" and valid_wb(raw.daylight_whitebalance):
        return {
            "use_camera_wb": False,
            "use_auto_wb": False,
            "user_wb": [float(value) for value in raw.daylight_whitebalance],
        }, "daylight"
    return {"use_camera_wb": False, "use_auto_wb": True}, "auto"


def channel_labels(raw: rawpy.RawPy) -> list[str]:
    description = raw.color_desc.decode("ascii", errors="replace")
    seen: dict[str, int] = {}
    labels = []
    for index in range(max(raw.num_colors, len(raw.black_level_per_channel))):
        base = description[index] if index < len(description) else f"C{index}"
        seen[base] = seen.get(base, 0) + 1
        labels.append(f"{base}{seen[base]}" if description.count(base) > 1 else base)
    return labels


def white_levels(raw: rawpy.RawPy, count: int) -> np.ndarray:
    camera = raw.camera_white_level_per_channel
    if camera is not None:
        values = np.asarray(camera, dtype=np.float32)
        if values.size >= count and np.all(values[:count] > 0):
            return values[:count]
    return np.full(count, float(raw.white_level), dtype=np.float32)


def extract_tiff_metadata(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".dng":
        return {}
    wanted = (
        "Make",
        "Model",
        "Software",
        "DateTime",
        "UniqueCameraModel",
        "LocalizedCameraModel",
        "DNGVersion",
        "DNGBackwardVersion",
        "AsShotNeutral",
        "CalibrationIlluminant1",
    )
    try:
        with tifffile.TiffFile(path) as tiff:
            tags = tiff.pages[0].tags
            return {
                name: json_safe(tags[name].value)
                for name in wanted
                if name in tags
            }
    except Exception:
        return {}


def extract_exiftool(path: Path) -> dict[str, Any]:
    executable = shutil.which("exiftool")
    if not executable:
        return {}
    try:
        result = subprocess.run(
            [executable, "-j", "-n", str(path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        payload = json.loads(result.stdout)
        return payload[0] if payload else {}
    except Exception as error:
        return {"_error": str(error)}


def collect_metadata(
    raw: rawpy.RawPy,
    path: Path,
    include_private_metadata: bool,
) -> dict[str, Any]:
    sizes = json_safe(raw.sizes)
    other = json_safe(raw.other)
    lens = json_safe(raw.lens)
    metadata = {
        "source": {
            "path": str(path) if include_private_metadata else path.name,
            "name": path.name,
            "extension": path.suffix.lower(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "absolute_path_included": include_private_metadata,
        },
        "decoder": {
            "rawpy_version": rawpy.__version__,
            "libraw_version": ".".join(str(part) for part in rawpy.libraw_version),
            "raw_type": str(raw.raw_type),
        },
        "image": sizes,
        "capture": other,
        "lens": lens,
        "sensor": {
            "num_colors": int(raw.num_colors),
            "color_description": raw.color_desc.decode("ascii", errors="replace"),
            "cfa_pattern": json_safe(raw.raw_pattern),
            "black_level_per_channel": json_safe(raw.black_level_per_channel),
            "camera_white_level_per_channel": json_safe(raw.camera_white_level_per_channel),
            "white_level": int(raw.white_level),
            "camera_white_balance": json_safe(raw.camera_whitebalance),
            "daylight_white_balance": json_safe(raw.daylight_whitebalance),
            "color_matrix": json_safe(raw.color_matrix),
            "rgb_xyz_matrix": json_safe(raw.rgb_xyz_matrix),
        },
        "container_metadata": extract_tiff_metadata(path),
        "exiftool": extract_exiftool(path),
        "privacy": {
            "private_metadata_included": include_private_metadata,
            "default_redactions": sorted(SENSITIVE_METADATA_KEYS),
        },
    }
    if not include_private_metadata:
        metadata["exiftool"] = redact_private_metadata(metadata["exiftool"])
    return metadata


def normalize_sample(
    values: np.ndarray,
    colors: np.ndarray,
    black: np.ndarray,
    white: np.ndarray,
) -> np.ndarray:
    channel = np.clip(colors.astype(np.intp), 0, len(black) - 1)
    denominator = np.maximum(1.0, white[channel] - black[channel])
    return np.clip((values.astype(np.float32) - black[channel]) / denominator, 0.0, 1.5)


def make_norm8(
    values: np.ndarray,
    colors: np.ndarray,
    black: np.ndarray,
    white: np.ndarray,
) -> np.ndarray:
    output = np.empty(values.shape, dtype=np.uint8)
    chunk_rows = 256
    for start in range(0, values.shape[0], chunk_rows):
        end = min(values.shape[0], start + chunk_rows)
        normalized = normalize_sample(values[start:end], colors[start:end], black, white)
        output[start:end] = np.clip(np.round(normalized * 255.0), 0, 255).astype(np.uint8)
    return output


def resize_luma(array: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(
        Image.fromarray(array, mode="L").resize(size, Image.Resampling.BOX),
        dtype=np.uint8,
    )


def analyze_sensor(raw: rawpy.RawPy, map_long_edge: int = 1000) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    values = raw.raw_image_visible
    colors = raw.raw_colors_visible
    count = max(int(np.max(colors)) + 1, len(raw.black_level_per_channel))
    labels = channel_labels(raw)
    description = raw.color_desc.decode("ascii", errors="replace")
    green_indices = [
        index
        for index in range(count)
        if index < len(description) and description[index].upper() == "G"
    ]
    black = np.asarray(raw.black_level_per_channel[:count], dtype=np.float32)
    if black.size < count:
        black = np.pad(black, (0, count - black.size), mode="edge")
    white = white_levels(raw, count)

    requested_stride = max(1, int(math.ceil(math.sqrt(values.size / 6_000_000))))
    sample_stride = cfa_safe_stride(requested_stride, raw.raw_pattern)
    sampled_values = values[::sample_stride, ::sample_stride]
    sampled_colors = colors[::sample_stride, ::sample_stride]
    normalized = normalize_sample(sampled_values, sampled_colors, black, white)

    channel_stats: list[dict[str, Any]] = []
    for index in range(count):
        channel_values = normalized[sampled_colors == index]
        if not channel_values.size:
            continue
        channel_stats.append(
            {
                "index": index,
                "label": labels[index] if index < len(labels) else f"C{index}",
                "sample_count": int(channel_values.size),
                "black_level": float(black[index]),
                "white_level": float(white[index]),
                "p01": float(np.quantile(channel_values, 0.01)),
                "p50": float(np.quantile(channel_values, 0.50)),
                "p99": float(np.quantile(channel_values, 0.99)),
                "p995": float(np.quantile(channel_values, 0.995)),
                "p999": float(np.quantile(channel_values, 0.999)),
                "near_clip_fraction": float(np.mean(channel_values >= 0.98)),
                "clipped_fraction": float(np.mean(channel_values >= 0.995)),
                "deep_shadow_fraction": float(np.mean(channel_values <= 0.01)),
                "headroom_stops_p999": float(
                    max(0.0, -math.log2(max(float(np.quantile(channel_values, 0.999)), 1e-6)))
                ),
            }
        )

    p995 = float(np.quantile(normalized, 0.995))
    p999 = float(np.quantile(normalized, 0.999))
    clipped_fraction = float(np.mean(normalized >= 0.995))
    near_clip_fraction = float(np.mean(normalized >= 0.98))
    if green_indices:
        shadow_samples = normalized[np.isin(sampled_colors, green_indices)]
    else:
        shadow_samples = normalized
    if not shadow_samples.size:
        shadow_samples = normalized
    deep_shadow_fraction = float(np.mean(shadow_samples <= 0.01))
    shadow_fraction = float(np.mean(shadow_samples <= 0.05))
    limiting_channel = max(channel_stats, key=lambda item: item["p995"])
    headroom_stops = min(item["headroom_stops_p999"] for item in channel_stats)
    suggested_ev = clamp(
        math.log2(0.90 / max(float(limiting_channel["p995"]), 0.02)),
        -1.5,
        1.5,
    )
    max_channel_clip = max(item["clipped_fraction"] for item in channel_stats)
    max_channel_near_clip = max(item["near_clip_fraction"] for item in channel_stats)
    if max_channel_clip > 0.001 or max_channel_near_clip > 0.015:
        suggested_ev = min(suggested_ev, -0.15)

    height, width = values.shape
    analysis_long_edge = min(map_long_edge, max(32, max(width, height) // 4))
    if width >= height:
        map_width = analysis_long_edge
        map_height = max(1, round(height / width * analysis_long_edge))
    else:
        map_height = analysis_long_edge
        map_width = max(1, round(width / height * analysis_long_edge))
    map_size = (map_width, map_height)
    norm8 = make_norm8(values, colors, black, white)
    norm_small = resize_luma(norm8, map_size)
    diversity = np.zeros((map_height, map_width), dtype=np.uint8)
    clip_strength = np.zeros((map_height, map_width), dtype=np.uint8)
    green_maps: list[np.ndarray] = []
    for index in range(count):
        channel_mask = np.where((norm8 >= 250) & (colors == index), 255, 0).astype(np.uint8)
        local = resize_luma(channel_mask, map_size)
        diversity += (local >= 8).astype(np.uint8)
        clip_strength = np.maximum(clip_strength, local)
        if index < len(description) and description[index] == "G":
            values_mask = np.where(colors == index, norm8, 0).astype(np.uint8)
            presence = np.where(colors == index, 255, 0).astype(np.uint8)
            value_small = resize_luma(values_mask, map_size).astype(np.float32)
            presence_small = resize_luma(presence, map_size).astype(np.float32)
            green_map = np.full(value_small.shape, np.nan, dtype=np.float32)
            np.divide(value_small, presence_small, out=green_map, where=presence_small >= 8.0)
            green_maps.append(green_map)
    partial_highlight = ((diversity >= 1) & (diversity <= 2)).astype(np.uint8) * 255
    severe_highlight = (diversity >= 3).astype(np.uint8) * 255
    if green_maps:
        stacked_green = np.stack(green_maps, axis=0)
        valid_green = np.isfinite(stacked_green)
        green_count = np.sum(valid_green, axis=0)
        green_sum = np.sum(np.where(valid_green, stacked_green, 0.0), axis=0)
        shadow_signal = np.divide(
            green_sum,
            np.maximum(green_count, 1),
            dtype=np.float32,
        )
        shadow_signal = np.where(green_count > 0, shadow_signal, norm_small.astype(np.float32) / 255.0)
        shadow_signal_basis = "green_cfa_block_signal"
    else:
        shadow_signal = norm_small.astype(np.float32) / 255.0
        shadow_signal_basis = "all_cfa_block_signal"
    deep_shadow = (shadow_signal <= 0.02).astype(np.uint8) * 255
    liftable_shadow = ((shadow_signal > 0.02) & (shadow_signal <= 0.12)).astype(np.uint8) * 255

    shadow_variation_proxy = None
    if len(green_maps) >= 2:
        shadow_region = (
            np.isfinite(green_maps[0])
            & np.isfinite(green_maps[1])
            & (shadow_signal <= 0.12)
        )
        if np.any(shadow_region):
            shadow_variation_proxy = float(
                np.median(np.abs(green_maps[0] - green_maps[1])[shadow_region])
            )

    if float(np.mean(severe_highlight > 0)) > 0.002:
        highlight_status = "部分区域多通道接近饱和，恢复空间有限"
    elif clipped_fraction > 0.0005:
        highlight_status = "存在单通道或局部裁切，可尝试高光重建"
    else:
        highlight_status = "未发现明显传感器级裁切"
    if deep_shadow_fraction > 0.30:
        shadow_status = "大量像素贴近黑电平，提亮后噪声风险较高"
    elif shadow_fraction > 0.35:
        shadow_status = "暗部占比较高，建议比较中性版与暗部展开版"
    else:
        shadow_status = "暗部没有大面积贴近黑电平"

    confidence = "high"
    caveats = []
    if shadow_variation_proxy is None:
        confidence = "medium"
        caveats.append("缺少可比较的双绿色通道局部数据，阴影噪声只能做亮度占比估算。")
    caveats.append("单张 RAW 无法精确测量传感器信噪比；阴影可恢复性是代理指标。")
    caveats.append("高光重建能否自然取决于场景颜色、镜头炫光和相机通道响应。")
    caveats.append("曝光建议由最接近饱和的 RAW 通道约束，白平衡和色彩矩阵仍可能改变最终高光。")

    analysis = {
        "schema": "raw-darkroom/sensor-analysis/v1",
        "measurement": {
            "sample_stride": sample_stride,
            "requested_sample_stride": requested_stride,
            "channel_stats": channel_stats,
            "global_p995": p995,
            "global_p999": p999,
            "near_clip_fraction": near_clip_fraction,
            "clipped_fraction": clipped_fraction,
            "deep_shadow_fraction": deep_shadow_fraction,
            "shadow_fraction_below_5_percent": shadow_fraction,
            "estimated_highlight_headroom_stops": headroom_stops,
            "limiting_highlight_channel": limiting_channel["label"],
            "limiting_highlight_channel_p995": limiting_channel["p995"],
            "shadow_green_variation_proxy": shadow_variation_proxy,
            "shadow_signal_basis": shadow_signal_basis,
            "local_partial_highlight_fraction": float(np.mean(partial_highlight > 0)),
            "local_severe_highlight_fraction": float(np.mean(severe_highlight > 0)),
        },
        "assessment": {
            "highlight_status": highlight_status,
            "shadow_status": shadow_status,
            "suggested_base_exposure_ev": suggested_ev,
            "confidence": confidence,
            "caveats": caveats,
        },
    }
    maps = {
        "normalized": norm_small,
        "partial_highlight": partial_highlight,
        "severe_highlight": severe_highlight,
        "deep_shadow": deep_shadow,
        "liftable_shadow": liftable_shadow,
        "clip_strength": clip_strength,
    }
    return analysis, maps


def resize_for_map(image: Image.Image, target: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image, target, Image.Resampling.LANCZOS)


def find_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def render_analysis_map(
    path: Path,
    preview: Image.Image,
    first_mask: np.ndarray,
    second_mask: np.ndarray,
    title: str,
    first_label: str,
    second_label: str,
    first_color: tuple[int, int, int],
    second_color: tuple[int, int, int],
) -> None:
    width = min(1600, max(1000, preview.width))
    height = max(1, round(preview.height / preview.width * width))
    base = resize_for_map(preview, (width, height)).convert("RGBA")
    base = ImageEnhance.Brightness(base).enhance(0.55)
    first = np.asarray(
        Image.fromarray(first_mask, mode="L").resize((width, height), Image.Resampling.BILINEAR)
    )
    second = np.asarray(
        Image.fromarray(second_mask, mode="L").resize((width, height), Image.Resampling.BILINEAR)
    )
    overlay = np.zeros((height, width, 4), dtype=np.uint8)
    overlay[first > 24] = (*first_color, 145)
    overlay[second > 24] = (*second_color, 205)
    base.alpha_composite(Image.fromarray(overlay, mode="RGBA"))

    header = 142
    canvas = Image.new("RGBA", (width, height + header), (8, 14, 19, 255))
    canvas.alpha_composite(base, (0, header))
    draw = ImageDraw.Draw(canvas)
    draw.text((28, 18), title, font=find_font(30, True), fill=(243, 246, 247))
    draw.ellipse((28, 92, 48, 112), fill=first_color)
    draw.text((58, 82), first_label, font=find_font(20), fill=(175, 185, 190))
    second_x = max(520, width // 2)
    draw.ellipse((second_x, 92, second_x + 20, 112), fill=second_color)
    draw.text((second_x + 30, 82), second_label, font=find_font(20), fill=(175, 185, 190))
    canvas.convert("RGB").save(path, icc_profile=SRGB_ICC_PROFILE)


def apply_tone_chunked(linear16: np.ndarray, recipe: dict[str, Any]) -> np.ndarray:
    output = np.empty_like(linear16, dtype=np.uint16)
    shoulder = float(recipe["shoulder"])
    shadow_gamma = float(recipe["shadow_gamma"])
    contrast = float(recipe["contrast"])
    black_lift = float(recipe["black_lift"])
    saturation = float(recipe["saturation"])
    warmth = float(recipe["warmth"])
    highlight_desaturation = float(recipe["highlight_desaturation"])
    channel_scale = np.array([1.0 + warmth, 1.0, 1.0 - warmth], dtype=np.float32)
    for start in range(0, linear16.shape[0], 192):
        end = min(linear16.shape[0], start + 192)
        x = linear16[start:end].astype(np.float32) / 65535.0
        x *= channel_scale[None, None, :]
        x = np.maximum(x, 0.0)
        x = x * (1.0 + shoulder) / np.maximum(1.0 + shoulder * x, 1e-6)
        x = np.clip(black_lift + (1.0 - black_lift) * x, 0.0, 1.0)
        x = np.power(x, shadow_gamma)
        smooth = x * x * (3.0 - 2.0 * x)
        x = np.clip(x + contrast * (smooth - x), 0.0, 1.0)
        luma = x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722
        x = luma[..., None] + saturation * (x - luma[..., None])
        highlight_weight = np.clip((luma - 0.68) / 0.30, 0.0, 1.0) * highlight_desaturation
        x = x * (1.0 - highlight_weight[..., None]) + luma[..., None] * highlight_weight[..., None]
        x = np.clip(x, 0.0, 1.0)
        encoded = np.where(
            x <= 0.0031308,
            12.92 * x,
            1.055 * np.power(x, 1.0 / 2.4) - 0.055,
        )
        output[start:end] = np.clip(np.round(encoded * 65535.0), 0, 65535).astype(np.uint16)
    return output


def write_tiff(path: Path, image16: np.ndarray, description: dict[str, Any]) -> None:
    ascii_description = json.dumps(json_safe(description), ensure_ascii=True)
    try:
        tifffile.imwrite(
            path,
            image16,
            photometric="rgb",
            compression="deflate",
            iccprofile=SRGB_ICC_PROFILE,
            metadata=None,
            description=ascii_description,
        )
    except Exception:
        tifffile.imwrite(
            path,
            image16,
            photometric="rgb",
            compression=None,
            iccprofile=SRGB_ICC_PROFILE,
            metadata=None,
            description=ascii_description,
        )


def develop_candidates(
    raw: rawpy.RawPy,
    output_dir: Path,
    recipes: list[dict[str, Any]],
    suggested_ev: float,
    wb_requested: str,
    half_size: bool,
    jpeg_quality: int,
    orientation: int,
) -> tuple[list[dict[str, Any]], list[Image.Image]]:
    wb_kwargs, wb_effective = white_balance_kwargs(raw, wb_requested)
    outputs = []
    previews = []
    for recipe in recipes:
        effective_ev = clamp(suggested_ev + float(recipe["ev_offset"]), -2.0, 2.0)
        params = {
            **recipe,
            "effective_exposure_ev": effective_ev,
            "white_balance_requested": wb_requested,
            "white_balance_effective": wb_effective,
            "output_color_space": "sRGB",
            "tiff_encoding": "16-bit display-referred sRGB transfer",
            "jpeg_encoding": "8-bit display-referred sRGB",
            "half_size": half_size,
            "libraw_orientation_applied": orientation,
        }
        linear16 = raw.postprocess(
            half_size=half_size,
            output_bps=16,
            output_color=rawpy.ColorSpace.sRGB,
            gamma=(1.0, 1.0),
            no_auto_bright=True,
            bright=1.0,
            highlight_mode=rawpy.HighlightMode.ReconstructDefault,
            exp_shift=2.0**effective_ev,
            exp_preserve_highlights=float(recipe["highlight_preserve"]),
            user_flip=0,
            **wb_kwargs,
        )
        developed16 = apply_tone_chunked(linear16, recipe)
        developed16 = orient_array(developed16, orientation)
        stem = recipe["id"]
        tiff_path = output_dir / f"{stem}.tiff"
        jpeg_path = output_dir / f"{stem}.jpg"
        write_tiff(tiff_path, developed16, params)
        jpeg8 = ((developed16.astype(np.uint32) + 128) // 257).astype(np.uint8)
        jpeg_image = Image.fromarray(jpeg8, mode="RGB")
        jpeg_image.save(
            jpeg_path,
            quality=jpeg_quality,
            subsampling=0,
            icc_profile=SRGB_ICC_PROFILE,
        )
        preview = jpeg_image.copy()
        preview.thumbnail((1200, 800), Image.Resampling.LANCZOS)
        previews.append(preview)
        outputs.append(
            {
                **params,
                "tiff": tiff_path.name,
                "jpeg": jpeg_path.name,
                "width": int(developed16.shape[1]),
                "height": int(developed16.shape[0]),
            }
        )
        del linear16, developed16, jpeg8, jpeg_image
    return outputs, previews


def render_contact_sheet(
    path: Path,
    previews: list[Image.Image],
    candidates: list[dict[str, Any]],
    source_name: str,
) -> None:
    width, height = 1800, 1280
    canvas = Image.new("RGB", (width, height), (9, 15, 20))
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 42), "RAW DARKROOM / 数字底片实验室", font=find_font(40, True), fill=(242, 245, 246))
    draw.text((64, 96), source_name, font=find_font(24), fill=(145, 160, 168))
    boxes = [
        (64, 158, 882, 676),
        (918, 158, 1736, 676),
        (64, 724, 882, 1242),
        (918, 724, 1736, 1242),
    ]
    for preview, candidate, box in zip(previews, candidates, boxes):
        x1, y1, x2, y2 = box
        image_box = (x1, y1, x2, y2 - 86)
        image_width = x2 - x1
        image_height = image_box[3] - image_box[1]
        draw.rectangle(image_box, fill=(5, 10, 14))
        fitted = ImageOps.contain(preview, (image_width, image_height), Image.Resampling.LANCZOS)
        image_x = x1 + (image_width - fitted.width) // 2
        image_y = y1 + (image_height - fitted.height) // 2
        canvas.paste(fitted, (image_x, image_y))
        draw.rectangle(box, outline=(47, 70, 81), width=2)
        draw.rectangle((x1, y2 - 86, x2, y2), fill=(14, 24, 31))
        draw.text(
            (x1 + 20, y2 - 72),
            f"{candidate['name_zh']} / {candidate['name_en']}",
            font=find_font(27, True),
            fill=(242, 245, 246),
        )
        draw.text(
            (x2 - 20, y2 - 68),
            f"{candidate['effective_exposure_ev']:+.2f} EV",
            font=find_font(23),
            fill=(255, 174, 89),
            anchor="ra",
        )
    canvas.save(path, quality=94, icc_profile=SRGB_ICC_PROFILE)


def percentage(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.3f}%"


def render_report(
    path: Path,
    metadata: dict[str, Any],
    analysis: dict[str, Any],
    candidates: list[dict[str, Any]],
    validation: dict[str, Any],
) -> None:
    measure = analysis["measurement"]
    assess = analysis["assessment"]
    capture = metadata.get("capture", {})
    lens = metadata.get("lens", {})
    image = metadata.get("image", {})
    candidate_html = "".join(
        f"""
        <figure>
          <img src="{html.escape(candidate['jpeg'])}" alt="{html.escape(candidate['name_zh'])}">
          <figcaption><b>{html.escape(candidate['name_zh'])}</b> / {html.escape(candidate['name_en'])}
          <span>{candidate['effective_exposure_ev']:+.2f} EV</span></figcaption>
        </figure>
        """
        for candidate in candidates
    )
    channel_rows = "".join(
        f"<tr><td>{html.escape(item['label'])}</td><td>{item['p995']:.4f}</td><td>{item['p999']:.4f}</td>"
        f"<td>{item['headroom_stops_p999']:.2f}</td>"
        f"<td>{percentage(item['near_clip_fraction'])}</td><td>{percentage(item['clipped_fraction'])}</td>"
        f"<td>{percentage(item['deep_shadow_fraction'])}</td></tr>"
        for item in measure["channel_stats"]
    )
    caveats = "".join(f"<li>{html.escape(item)}</li>" for item in assess["caveats"])
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAW Darkroom Report</title>
<style>
:root{{--bg:#081016;--panel:#0e1921;--line:#29414d;--text:#f1f4f5;--muted:#98a8b0;--cyan:#54d5e3;--amber:#ffae59;--red:#ec6356}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font-family:"Microsoft YaHei",Arial,sans-serif;line-height:1.65}}
main{{width:min(1180px,calc(100% - 36px));margin:38px auto 72px}} header{{border-bottom:1px solid var(--line);padding-bottom:24px;margin-bottom:34px}}
h1{{font-size:clamp(34px,6vw,62px);margin:0;letter-spacing:0}} h2{{margin:44px 0 18px;font-size:25px}} p{{color:var(--muted)}} .eyebrow{{color:var(--cyan);font:700 13px Consolas,monospace}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line)}} .metric{{padding:18px;border-right:1px solid var(--line)}} .metric:last-child{{border:0}} .metric b{{display:block;font-size:26px}} .metric span{{color:var(--muted);font-size:12px}}
.maps,.candidates{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} figure{{margin:0;border:1px solid var(--line);background:var(--panel)}} figure img{{display:block;width:100%;height:auto}} figcaption{{padding:13px 16px;color:var(--muted)}} figcaption b{{color:var(--text)}} figcaption span{{float:right;color:var(--amber)}}
table{{width:100%;border-collapse:collapse;background:var(--panel)}} th,td{{padding:11px 13px;text-align:left;border-bottom:1px solid var(--line)}} th{{color:var(--muted);font-size:12px}} .status{{border-left:4px solid var(--cyan);background:var(--panel);padding:18px 22px}} code{{color:var(--amber)}} ul{{color:var(--muted)}}
@media(max-width:760px){{.metrics{{grid-template-columns:1fr 1fr}}.metric:nth-child(2){{border-right:0}}.maps,.candidates{{grid-template-columns:1fr}}}}
</style></head><body><main>
<header><div class="eyebrow">RAW DARKROOM / SENSOR ANALYSIS + DEVELOPMENT</div><h1>数字底片实验室</h1>
<p>{html.escape(metadata['source']['name'])} · {image.get('width', 0)}×{image.get('height', 0)} · validation: {validation['status']}</p></header>
<section class="metrics">
<div class="metric"><span>建议基础曝光</span><b>{assess['suggested_base_exposure_ev']:+.2f} EV</b></div>
    <div class="metric"><span>限制通道 / 高光余量</span><b>{html.escape(measure['limiting_highlight_channel'])} · {measure['estimated_highlight_headroom_stops']:.2f}</b></div>
<div class="metric"><span>近裁切像素</span><b>{percentage(measure['near_clip_fraction'])}</b></div>
<div class="metric"><span>深暗部像素</span><b>{percentage(measure['deep_shadow_fraction'])}</b></div>
</section>
<h2>判断</h2><div class="status"><b>{html.escape(assess['highlight_status'])}</b><br>
{html.escape(assess['shadow_status'])}<br>置信度：{html.escape(assess['confidence'])}</div>
    <h2>传感器通道</h2><table><thead><tr><th>通道</th><th>P99.5</th><th>P99.9</th><th>余量/stop</th><th>近裁切</th><th>裁切</th><th>深暗部</th></tr></thead><tbody>{channel_rows}</tbody></table>
<h2>分析图</h2><div class="maps">
<figure><img src="highlight-map.png"><figcaption>高光通道裁切分布</figcaption></figure>
<figure><img src="shadow-map.png"><figcaption>暗部信号分布</figcaption></figure></div>
<h2>四套显影候选</h2><div class="candidates">{candidate_html}</div>
<h2>拍摄元数据</h2><table>
<tr><th>ISO</th><td>{capture.get('iso_speed', 'n/a')}</td><th>快门</th><td>{capture.get('shutter_speed', 'n/a')}</td></tr>
<tr><th>光圈</th><td>{capture.get('aperture', 'n/a')}</td><th>焦距</th><td>{capture.get('focal_length', lens.get('min_focal', 'n/a'))}</td></tr>
<tr><th>镜头</th><td colspan="3">{html.escape(str(lens.get('model', '') or 'n/a'))}</td></tr></table>
    <h2>限制</h2><ul>{caveats}<li>候选 TIFF 是 16-bit、显示参考的 sRGB 传递编码，不是未调色的场景线性文件。</li>
    <li>TIFF、JPEG、接触表与分析图均嵌入 sRGB ICC；默认不输出绝对源路径、GPS、序列号和所有者字段。</li></ul>
</main></body></html>"""
    path.write_text(document, encoding="utf-8")


def validate_outputs(
    output_dir: Path,
    candidates: list[dict[str, Any]],
    analysis: dict[str, Any],
    require_report: bool = False,
) -> dict[str, Any]:
    expected = [
        "metadata.json",
        "analysis.json",
        "development-parameters.json",
        "sensor-preview.jpg",
        "highlight-map.png",
        "shadow-map.png",
        "contact-sheet.jpg",
    ]
    if require_report:
        expected.append("report.html")
    for candidate in candidates:
        expected.extend([candidate["tiff"], candidate["jpeg"]])
    files = {}
    valid = True
    reference_size = None
    for filename in expected:
        path = output_dir / filename
        exists = path.is_file() and path.stat().st_size > 0
        files[filename] = {
            "exists": exists,
            "bytes": path.stat().st_size if exists else 0,
            "sha256": sha256(path) if exists else None,
        }
        valid = valid and exists
    tiff_checks = []
    jpeg_checks = []
    for candidate in candidates:
        path = output_dir / candidate["tiff"]
        try:
            with tifffile.TiffFile(path) as tiff:
                page = tiff.pages[0]
                shape = tuple(page.shape)
                dtype = str(page.dtype)
                icc_tag = page.tags.get(34675)
                icc_present = icc_tag is not None and len(icc_tag.value) >= 128
            size = (shape[1], shape[0]) if len(shape) == 3 else None
            reference_size = reference_size or size
            same_size = size == reference_size
            check = (
                len(shape) == 3
                and shape[2] == 3
                and dtype == "uint16"
                and same_size
                and icc_present
            )
        except Exception:
            shape, dtype, same_size, icc_present, check = (), "", False, False, False
        tiff_checks.append(
            {
                "file": candidate["tiff"],
                "shape": shape,
                "dtype": dtype,
                "same_size": same_size,
                "icc_profile": icc_present,
                "valid": check,
            }
        )
        valid = valid and check
        try:
            with Image.open(output_dir / candidate["jpeg"]) as jpeg:
                jpeg_icc = bool(jpeg.info.get("icc_profile"))
                jpeg_check = (
                    jpeg.mode == "RGB"
                    and jpeg.size == reference_size
                    and jpeg_icc
                )
                jpeg_size = jpeg.size
        except Exception:
            jpeg_check, jpeg_icc, jpeg_size = False, False, ()
        jpeg_checks.append(
            {
                "file": candidate["jpeg"],
                "size": jpeg_size,
                "icc_profile": jpeg_icc,
                "valid": jpeg_check,
            }
        )
        valid = valid and jpeg_check
    profile_checks = {}
    for filename in (
        "sensor-preview.jpg",
        "highlight-map.png",
        "shadow-map.png",
        "contact-sheet.jpg",
    ):
        try:
            with Image.open(output_dir / filename) as image:
                profile_checks[filename] = bool(image.info.get("icc_profile"))
        except Exception:
            profile_checks[filename] = False
    valid = valid and all(profile_checks.values())
    json_checks = {}
    for filename in ("metadata.json", "analysis.json", "development-parameters.json"):
        try:
            with (output_dir / filename).open("r", encoding="utf-8") as handle:
                json.loads(handle.read())
            json_checks[filename] = True
        except Exception:
            json_checks[filename] = False
    valid = valid and all(json_checks.values())
    report_check = None
    if require_report:
        try:
            report_text = (output_dir / "report.html").read_text(encoding="utf-8")
            image_refs = re.findall(r'<img\s+[^>]*src="([^"]+)"', report_text)
            refs_valid = bool(image_refs) and all(
                not Path(reference).is_absolute()
                and (output_dir / reference).is_file()
                for reference in image_refs
            )
            report_check = {
                "closing_html": "</html>" in report_text.lower(),
                "image_reference_count": len(image_refs),
                "image_references_valid": refs_valid,
            }
        except Exception:
            report_check = {
                "closing_html": False,
                "image_reference_count": 0,
                "image_references_valid": False,
            }
        valid = valid and all(
            (
                report_check["closing_html"],
                report_check["image_references_valid"],
            )
        )
    measure = analysis["measurement"]
    finite_checks = all(
        math.isfinite(float(measure[key]))
        for key in (
            "global_p995",
            "global_p999",
            "near_clip_fraction",
            "clipped_fraction",
            "deep_shadow_fraction",
        )
    )
    candidate_count_valid = len(candidates) == 4 and len({item["id"] for item in candidates}) == 4
    valid = valid and finite_checks and candidate_count_valid
    return {
        "status": "pass" if valid else "fail",
        "files": files,
        "tiff_checks": tiff_checks,
        "jpeg_checks": jpeg_checks,
        "embedded_profile_checks": profile_checks,
        "json_checks": json_checks,
        "report_check": report_check,
        "sensor_metrics_finite": finite_checks,
        "candidate_count": len(candidates),
        "candidate_count_valid": candidate_count_valid,
    }


def load_recipes() -> list[dict[str, Any]]:
    with RECIPES_PATH.open("r", encoding="utf-8") as handle:
        recipes = json.load(handle)
    if len(recipes) != 4:
        raise ValueError("第一版必须正好包含四套显影方案")
    return recipes


def prepare_output_dir(
    output_dir: Path,
    recipes: list[dict[str, Any]],
    overwrite: bool,
) -> None:
    managed_files = set(BASE_OUTPUT_FILES)
    for recipe in recipes:
        managed_files.update((f"{recipe['id']}.tiff", f"{recipe['id']}.jpg"))
    if output_dir.exists():
        entries = list(output_dir.iterdir())
        if entries and not overwrite:
            raise FileExistsError(
                f"输出目录不是空目录：{output_dir}。确认内容可替换后使用 --overwrite。"
            )
        unknown = [
            entry.name
            for entry in entries
            if not entry.is_file() or entry.name not in managed_files
        ]
        if unknown:
            raise ValueError(
                "输出目录包含非 RAW Darkroom 文件，拒绝覆盖："
                + ", ".join(sorted(unknown))
            )
        for entry in entries:
            entry.unlink()
    else:
        output_dir.mkdir(parents=True)


def run(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到输入文件：{input_path}")
    if input_path.suffix.lower() in PROCESSED_EXTENSIONS:
        raise ValueError("输入是已处理图像，不是相机 RAW。请提供 DNG、CR2/CR3、NEF、ARW、RW2、RAF 等 RAW 文件。")
    recipes = load_recipes()
    output_dir = Path(args.output_dir).expanduser().resolve()
    prepare_output_dir(output_dir, recipes, args.overwrite)

    with rawpy.imread(str(input_path)) as raw:
        orientation = int(raw.sizes.flip)
        metadata = collect_metadata(raw, input_path, args.include_private_metadata)
        analysis, maps = analyze_sensor(raw)
        wb_kwargs, wb_effective = white_balance_kwargs(raw, args.white_balance)
        preview_array = raw.postprocess(
            half_size=True,
            output_bps=8,
            output_color=rawpy.ColorSpace.sRGB,
            no_auto_bright=False,
            highlight_mode=rawpy.HighlightMode.Blend,
            user_flip=0,
            **wb_kwargs,
        )
        preview_array = orient_array(preview_array, orientation)
        maps = {
            name: orient_array(array, orientation)
            for name, array in maps.items()
        }
        preview = Image.fromarray(preview_array, mode="RGB")
        preview.save(
            output_dir / "sensor-preview.jpg",
            quality=args.jpeg_quality,
            subsampling=0,
            icc_profile=SRGB_ICC_PROFILE,
        )
        render_analysis_map(
            output_dir / "highlight-map.png",
            preview,
            maps["partial_highlight"],
            maps["severe_highlight"],
            "高光通道裁切 / Highlight Recoverability",
            "局部通道接近饱和",
            "多通道接近饱和",
            (255, 172, 82),
            (235, 72, 62),
        )
        render_analysis_map(
            output_dir / "shadow-map.png",
            preview,
            maps["liftable_shadow"],
            maps["deep_shadow"],
            "暗部信号分布 / Shadow Recoverability",
            "可尝试展开",
            "贴近黑电平",
            (74, 205, 221),
            (164, 92, 205),
        )
        candidates, candidate_previews = develop_candidates(
            raw,
            output_dir,
            recipes,
            analysis["assessment"]["suggested_base_exposure_ev"],
            args.white_balance,
            args.half_size,
            args.jpeg_quality,
            orientation,
        )

    metadata["processing"] = {
        "white_balance_requested": args.white_balance,
        "white_balance_effective": wb_effective,
        "half_size_candidates": args.half_size,
        "jpeg_quality": args.jpeg_quality,
        "libraw_orientation_applied": orientation,
        "embedded_color_profile": "sRGB",
        "private_metadata_included": args.include_private_metadata,
    }
    write_json(output_dir / "metadata.json", metadata)
    write_json(output_dir / "analysis.json", analysis)
    parameters = {
        "schema": "raw-darkroom/development-parameters/v1",
        "source": metadata["source"],
        "base_exposure_ev": analysis["assessment"]["suggested_base_exposure_ev"],
        "white_balance_requested": args.white_balance,
        "white_balance_effective": wb_effective,
        "candidates": candidates,
    }
    write_json(output_dir / "development-parameters.json", parameters)
    render_contact_sheet(output_dir / "contact-sheet.jpg", candidate_previews, candidates, input_path.name)
    preliminary_validation = validate_outputs(output_dir, candidates, analysis)
    render_report(output_dir / "report.html", metadata, analysis, candidates, preliminary_validation)
    validation = validate_outputs(output_dir, candidates, analysis, require_report=True)
    write_json(output_dir / "validation.json", validation)

    result = {
        "output_dir": str(output_dir),
        "validation": validation["status"],
        "highlight_status": analysis["assessment"]["highlight_status"],
        "shadow_status": analysis["assessment"]["shadow_status"],
        "suggested_base_exposure_ev": analysis["assessment"]["suggested_base_exposure_ev"],
        "candidate_count": len(candidates),
        "white_balance_effective": wb_effective,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if validation["status"] == "pass" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="数字底片实验室：分析相机 RAW，并输出四套 16-bit TIFF/JPEG 显影候选。"
    )
    parser.add_argument("input")
    parser.add_argument("--output-dir", default="raw-darkroom-output")
    parser.add_argument(
        "--white-balance",
        choices=("camera", "auto", "daylight"),
        default="camera",
        help="优先使用相机白平衡；无有效数据时自动回退到 auto。",
    )
    parser.add_argument("--half-size", action="store_true", help="候选以半分辨率解码，用于快速预览。")
    parser.add_argument("--jpeg-quality", type=int, default=94)
    parser.add_argument(
        "--include-private-metadata",
        action="store_true",
        help="保留绝对路径、GPS、序列号和所有者字段；默认脱敏。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖仅包含 RAW Darkroom 已知结果文件的输出目录。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 70 <= args.jpeg_quality <= 100:
        print("ERROR: jpeg-quality 必须在 70–100 之间", file=sys.stderr)
        return 1
    try:
        return run(args)
    except rawpy.LibRawError as error:
        print(
            f"ERROR: LibRaw 无法解码该文件：{error}。请确认文件完整，并检查当前 LibRaw 是否支持这台相机。",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
