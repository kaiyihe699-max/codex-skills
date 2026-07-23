from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont

try:
    import rawpy
except Exception:  # pragma: no cover - optional dependency
    rawpy = None

try:
    import pytesseract
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None


SKILL_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = SKILL_DIR / "assets"
ICC_PATH = ASSETS_DIR / "srgb-display-profile.b64"
SUPPORTED_EXTENSIONS = {
    ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp",
    ".dng", ".nef", ".arw", ".cr2", ".cr3", ".rw2", ".raf", ".orf", ".pef",
}
RAW_EXTENSIONS = {".dng", ".nef", ".arw", ".cr2", ".cr3", ".rw2", ".raf", ".orf", ".pef"}


class FilmScanError(RuntimeError):
    pass


def load_srgb_icc() -> bytes:
    data = base64.b64decode(ICC_PATH.read_text(encoding="ascii"))
    if len(data) < 128 or data[36:40] != b"acsp":
        raise FilmScanError("Built-in sRGB ICC profile is invalid")
    return data


SRGB_ICC = load_srgb_icc()


def json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0.0, 1.0).astype(np.float32)
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0.0, 1.0).astype(np.float32)
    return np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(value, 1 / 2.4) - 0.055)


def rgb_luma(image: np.ndarray) -> np.ndarray:
    return 0.2126 * image[..., 0] + 0.7152 * image[..., 1] + 0.0722 * image[..., 2]


def normalize_array(array: np.ndarray) -> tuple[np.ndarray, int]:
    original_dtype = array.dtype
    if array.ndim == 2:
        array = np.repeat(array[..., None], 3, axis=2)
    if array.ndim == 3 and array.shape[0] in (3, 4) and array.shape[-1] not in (3, 4):
        array = np.moveaxis(array, 0, -1)
    if array.ndim != 3 or array.shape[2] < 3:
        raise FilmScanError(f"Unsupported image shape: {array.shape}")
    array = array[..., :3]
    if np.issubdtype(original_dtype, np.integer):
        info = np.iinfo(original_dtype)
        bit_depth = info.bits
        result = array.astype(np.float32) / float(info.max)
    else:
        result = array.astype(np.float32)
        finite = result[np.isfinite(result)]
        if finite.size == 0:
            raise FilmScanError("Image contains no finite samples")
        maximum = float(np.percentile(finite, 99.99))
        if maximum > 1.5:
            result /= maximum
        bit_depth = 32
    return np.clip(result, 0.0, 1.0), bit_depth


def read_image(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    extension = path.suffix.lower()
    if extension in RAW_EXTENSIONS:
        if rawpy is None:
            raise FilmScanError("rawpy is required for camera RAW film scans")
        with rawpy.imread(str(path)) as raw:
            array = raw.postprocess(
                output_bps=16,
                output_color=rawpy.ColorSpace.sRGB,
                gamma=(1.0, 1.0),
                no_auto_bright=True,
                use_camera_wb=True,
                user_flip=0,
            )
        image, bit_depth = normalize_array(array)
        return image, {
            "path": path.name,
            "extension": extension,
            "bit_depth": bit_depth,
            "input_linear": True,
            "decoder": "rawpy/LibRaw",
            "icc_present": False,
        }

    icc_present = False
    if extension in {".tif", ".tiff"}:
        array = tifffile.imread(path)
        try:
            with tifffile.TiffFile(path) as tif:
                icc_present = bool(tif.pages[0].tags.get(34675))
        except Exception:
            pass
    else:
        with Image.open(path) as opened:
            icc_present = bool(opened.info.get("icc_profile"))
            array = np.asarray(opened.convert("RGB"))
    image, bit_depth = normalize_array(array)
    return image, {
        "path": path.name,
        "extension": extension,
        "bit_depth": bit_depth,
        "input_linear": False,
        "decoder": "tifffile" if extension in {".tif", ".tiff"} else "Pillow",
        "icc_present": icc_present,
    }


def read_gray(path: Path) -> np.ndarray:
    image, _ = read_image(path)
    return rgb_luma(image).astype(np.float32)


def load_catalog(path: Path, external: Path | None = None) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = list(payload.get("profiles", []))
    if external:
        custom = json.loads(external.read_text(encoding="utf-8"))
        if "profiles" in custom:
            profiles.extend(custom["profiles"])
        else:
            profiles.append(custom)
    return profiles


def select_profile(profiles: list[dict[str, Any]], profile_id: str) -> dict[str, Any]:
    for profile in profiles:
        if profile.get("id") == profile_id:
            return profile
    available = ", ".join(sorted(str(p.get("id")) for p in profiles))
    raise FilmScanError(f"Unknown profile '{profile_id}'. Available: {available}")


def apply_capture_profile(image: np.ndarray, profile: dict[str, Any], input_linear: bool) -> np.ndarray:
    black = np.asarray(profile.get("black_level", [0, 0, 0]), dtype=np.float32)
    white = np.asarray(profile.get("white_level", [1, 1, 1]), dtype=np.float32)
    gain = np.asarray(profile.get("channel_gain", [1, 1, 1]), dtype=np.float32)
    matrix = np.asarray(profile.get("matrix", np.eye(3)), dtype=np.float32)
    flare = float(profile.get("flare", 0.0))
    normalized = (image - black) / np.maximum(white - black, 1e-6)
    normalized = np.clip(normalized - flare, 0.0, 1.0)
    if input_linear:
        linear = normalized
    else:
        gamma = float(profile.get("decode_gamma", 2.2))
        if abs(gamma - 2.2) < 0.05:
            linear = srgb_to_linear(normalized)
        else:
            linear = np.power(normalized, gamma)
    linear *= gain
    linear = np.einsum("...c,dc->...d", linear, matrix)
    return np.clip(linear, 0.0, 1.0).astype(np.float32)


def write_tiff(path: Path, image_linear: np.ndarray, description: dict[str, Any]) -> None:
    encoded = linear_to_srgb(image_linear)
    image16 = np.clip(np.round(encoded * 65535.0), 0, 65535).astype(np.uint16)
    kwargs = {
        "photometric": "rgb",
        "iccprofile": SRGB_ICC,
        "metadata": None,
        "description": json.dumps(json_safe(description), ensure_ascii=True),
    }
    try:
        tifffile.imwrite(path, image16, compression="deflate", **kwargs)
    except Exception:
        tifffile.imwrite(path, image16, compression=None, **kwargs)


def write_jpeg(path: Path, image_linear: np.ndarray, quality: int = 94) -> None:
    encoded = linear_to_srgb(image_linear)
    image8 = np.clip(np.round(encoded * 255.0), 0, 255).astype(np.uint8)
    Image.fromarray(image8, "RGB").save(
        path,
        quality=quality,
        subsampling=0,
        icc_profile=SRGB_ICC,
    )


def write_png_mask(path: Path, mask: np.ndarray) -> None:
    Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8), "L").save(path)


def ensure_output_dir(output_dir: Path, overwrite: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = [item for item in output_dir.iterdir() if item.name not in {".DS_Store"}]
    if existing and not overwrite:
        raise FilmScanError("Output directory must be empty. Use --overwrite only for prior Film Scan Lab output.")
    if overwrite:
        for item in existing:
            if item.is_file():
                item.unlink()
            elif item.is_dir() and item.name in {"frames"}:
                resolved = item.resolve()
                if output_dir.resolve() not in resolved.parents:
                    raise FilmScanError("Refusing to remove a directory outside the output folder")
                shutil.rmtree(item)


def parse_roi(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    parts = [int(item.strip()) for item in value.split(",")]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise FilmScanError("--base-roi must be x,y,width,height")
    return tuple(parts)  # type: ignore[return-value]


def make_edge_strip(image: np.ndarray) -> np.ndarray:
    encoded = linear_to_srgb(image)
    image8 = np.clip(encoded * 255, 0, 255).astype(np.uint8)
    h, w = image8.shape[:2]
    band = max(18, int(min(h, w) * 0.10))
    top = image8[:band]
    bottom = np.flip(image8[h - band :], axis=(0, 1))
    left = np.rot90(image8[:, :band], 1)
    right = np.rot90(image8[:, w - band :], 3)
    strips = []
    for strip in (top, bottom, left, right):
        if strip.shape[0] != band:
            strip = cv2.resize(strip, (strip.shape[1], band), interpolation=cv2.INTER_AREA)
        strips.append(strip)
    return np.concatenate(strips, axis=1)


def ocr_edge_code(
    strip: np.ndarray,
    explicit_code: str | None,
    tesseract_cmd: str | None,
) -> dict[str, Any]:
    if explicit_code:
        return {"status": "manual", "text": explicit_code.strip().upper(), "confidence": 1.0}
    command = tesseract_cmd or shutil.which("tesseract")
    if pytesseract is None or not command:
        return {
            "status": "unavailable",
            "text": "",
            "confidence": 0.0,
            "note": "Tesseract is unavailable; inspect edge-code-strip.png and rerun with --edge-code.",
        }
    pytesseract.pytesseract.tesseract_cmd = command
    gray = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 4)).apply(gray)
    variants = [gray, cv2.bitwise_not(gray)]
    texts: list[str] = []
    confidences: list[float] = []
    config = "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-+ "
    for variant in variants:
        data = pytesseract.image_to_data(variant, config=config, output_type=pytesseract.Output.DICT)
        parts = []
        local_conf = []
        for text_value, confidence_value in zip(data.get("text", []), data.get("conf", [])):
            cleaned = re.sub(r"[^A-Z0-9+\-]", "", str(text_value).upper())
            try:
                confidence = float(confidence_value)
            except (TypeError, ValueError):
                confidence = -1.0
            if cleaned:
                parts.append(cleaned)
                if confidence >= 0:
                    local_conf.append(confidence / 100.0)
        texts.append(" ".join(parts))
        confidences.append(float(np.mean(local_conf)) if local_conf else 0.0)
    best = int(np.argmax(confidences))
    return {
        "status": "recognized" if texts[best] else "not-found",
        "text": texts[best],
        "confidence": confidences[best],
        "engine": "tesseract",
    }


def match_film_profile(
    profiles: list[dict[str, Any]],
    edge_text: str,
    requested_id: str | None,
    film_type: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if requested_id:
        profile = select_profile(profiles, requested_id)
        return profile, {"method": "explicit", "confidence": 1.0, "matched_pattern": requested_id}
    normalized = re.sub(r"\s+", " ", edge_text.upper()).strip()
    matches: list[tuple[int, dict[str, Any], str]] = []
    for profile in profiles:
        for pattern in profile.get("patterns", []):
            if str(pattern).upper() in normalized and pattern:
                matches.append((len(str(pattern)), profile, str(pattern)))
    if matches:
        matches.sort(key=lambda item: item[0], reverse=True)
        _, profile, pattern = matches[0]
        return profile, {"method": "edge-code", "confidence": 0.9, "matched_pattern": pattern}
    fallback = {
        "color-negative": "generic-c41",
        "bw-negative": "generic-bw-negative",
        "slide": "generic-slide",
    }.get(film_type, "generic-c41")
    profile = select_profile(profiles, fallback)
    return profile, {"method": "generic-fallback", "confidence": 0.4, "matched_pattern": None}


def align_image(reference: np.ndarray, moving: np.ndarray) -> tuple[np.ndarray, list[list[float]], float]:
    if moving.shape != reference.shape:
        moving = cv2.resize(moving, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    ref_gray = np.clip(rgb_luma(reference) * 255, 0, 255).astype(np.uint8)
    mov_gray = np.clip(rgb_luma(moving) * 255, 0, 255).astype(np.uint8)
    warp = np.eye(2, 3, dtype=np.float32)
    score = 0.0
    try:
        score, warp = cv2.findTransformECC(
            ref_gray,
            mov_gray,
            warp,
            cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5),
            None,
            3,
        )
        aligned = cv2.warpAffine(
            moving,
            warp,
            (reference.shape[1], reference.shape[0]),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REFLECT,
        )
    except cv2.error:
        aligned = moving
        warp = np.eye(2, 3, dtype=np.float32)
    return np.clip(aligned, 0, 1), warp.tolist(), float(score)


def merge_hdr_scans(images: list[np.ndarray]) -> tuple[np.ndarray, dict[str, Any]]:
    if len(images) == 1:
        return images[0], {"enabled": False, "scan_count": 1, "scales": [1.0], "alignments": []}
    reference = images[0]
    aligned_images = [reference]
    alignments = [{"warp": [[1, 0, 0], [0, 1, 0]], "score": 1.0}]
    for moving in images[1:]:
        aligned, warp, score = align_image(reference, moving)
        aligned_images.append(aligned)
        alignments.append({"warp": warp, "score": score})

    ref_luma = rgb_luma(reference)
    scales = [1.0]
    normalized = [reference]
    for image in aligned_images[1:]:
        luma = rgb_luma(image)
        valid = (ref_luma > 0.03) & (ref_luma < 0.92) & (luma > 0.03) & (luma < 0.92)
        if np.count_nonzero(valid) < 100:
            scale = float(np.median(luma) / max(np.median(ref_luma), 1e-6))
        else:
            scale = float(np.median(luma[valid] / np.maximum(ref_luma[valid], 1e-6)))
        scale = float(np.clip(scale, 0.05, 20.0))
        scales.append(scale)
        normalized.append(np.clip(image / scale, 0, 4))

    numerator = np.zeros_like(reference, dtype=np.float64)
    denominator = np.zeros(reference.shape[:2], dtype=np.float64)
    for source, radiance in zip(aligned_images, normalized):
        luma = rgb_luma(source)
        weight = 1.0 - np.abs(luma - 0.5) * 2.0
        weight = np.clip(weight, 0.03, 1.0)
        weight[(luma <= 0.005) | (luma >= 0.995)] = 0.005
        numerator += radiance.astype(np.float64) * weight[..., None]
        denominator += weight
    merged = numerator / np.maximum(denominator[..., None], 1e-8)
    p999 = float(np.percentile(merged, 99.9))
    if p999 > 1.0:
        merged /= p999
    return np.clip(merged, 0, 1).astype(np.float32), {
        "enabled": True,
        "mode": "relative-linear-radiance",
        "scan_count": len(images),
        "scales": scales,
        "alignments": alignments,
    }


def estimate_base(
    image: np.ndarray,
    roi: tuple[int, int, int, int] | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    h, w = image.shape[:2]
    if roi:
        x, y, rw, rh = roi
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + rw), min(h, y + rh)
        if x1 <= x0 or y1 <= y0:
            raise FilmScanError("Base ROI is outside the image")
        samples = image[y0:y1, x0:x1].reshape(-1, 3)
        method = "manual-roi"
        sample_fraction = samples.shape[0] / float(h * w)
    else:
        edge = max(8, int(min(h, w) * 0.09))
        mask = np.zeros((h, w), dtype=bool)
        mask[:edge] = True
        mask[-edge:] = True
        mask[:, :edge] = True
        mask[:, -edge:] = True
        luma = rgb_luma(image)
        gradient = cv2.magnitude(
            cv2.Sobel(luma, cv2.CV_32F, 1, 0, ksize=3),
            cv2.Sobel(luma, cv2.CV_32F, 0, 1, ksize=3),
        )
        edge_luma = luma[mask]
        edge_gradient = gradient[mask]
        lum_threshold = float(np.percentile(edge_luma, 58))
        grad_threshold = float(np.percentile(edge_gradient, 45))
        selected = mask & (luma >= lum_threshold) & (gradient <= grad_threshold)
        samples = image[selected]
        if samples.shape[0] < 100:
            samples = image[mask]
        method = "auto-edge"
        sample_fraction = samples.shape[0] / float(h * w)
    base = np.median(samples, axis=0).astype(np.float32)
    mad = np.median(np.abs(samples - base), axis=0)
    consistency = float(1.0 - np.clip(np.mean(mad) / max(float(np.mean(base)), 1e-6), 0, 1))
    confidence = float(np.clip(consistency * min(1.0, sample_fraction / 0.015), 0, 1))
    return np.clip(base, 1e-5, 1.0), {
        "method": method,
        "base_rgb_linear": base.tolist(),
        "base_density": (-np.log10(np.maximum(base, 1e-6))).tolist(),
        "sample_fraction": sample_fraction,
        "channel_mad": mad.tolist(),
        "confidence": confidence,
        "roi": list(roi) if roi else None,
    }


def infer_film_type(image: np.ndarray, base_rgb: np.ndarray) -> tuple[str, dict[str, Any]]:
    sample = image[:: max(1, image.shape[0] // 400), :: max(1, image.shape[1] // 400)].reshape(-1, 3)
    corr = np.corrcoef(sample.T)
    corr_value = float(np.nanmean([corr[0, 1], corr[0, 2], corr[1, 2]]))
    warm_mask = bool(base_rgb[0] > base_rgb[1] * 1.07 and base_rgb[1] > base_rgb[2] * 1.08)
    base_luma = float(np.dot(base_rgb, [0.2126, 0.7152, 0.0722]))
    if warm_mask:
        film_type = "color-negative"
        confidence = min(0.95, 0.62 + (base_rgb[0] - base_rgb[2]) * 0.7)
    elif corr_value > 0.992 and base_luma > 0.35:
        film_type = "bw-negative"
        confidence = min(0.92, 0.65 + (corr_value - 0.992) * 20)
    else:
        film_type = "slide"
        confidence = 0.55
    return film_type, {
        "method": "heuristic",
        "confidence": float(np.clip(confidence, 0, 1)),
        "base_luma": base_luma,
        "base_warm_mask": warm_mask,
        "channel_correlation": corr_value,
    }


def content_region(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    border = max(2, int(min(h, w) * 0.08))
    return image[border : h - border, border : w - border]


def stretch_channels(image: np.ndarray, low_q: float, high_q: float) -> tuple[np.ndarray, dict[str, Any]]:
    region = content_region(image).reshape(-1, 3)
    lows = np.percentile(region, low_q, axis=0).astype(np.float32)
    highs = np.percentile(region, high_q, axis=0).astype(np.float32)
    stretched = (image - lows) / np.maximum(highs - lows, 1e-6)
    return np.clip(stretched, 0, 1), {"low_percentile": low_q, "high_percentile": high_q, "lows": lows.tolist(), "highs": highs.tolist()}


def invert_film(
    image: np.ndarray,
    film_type: str,
    base_rgb: np.ndarray,
    profile: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    if film_type == "slide":
        positive, stretch = stretch_channels(image, 0.2, 99.8)
        return positive, {"mode": "positive-normalization", "stretch": stretch}

    density = -np.log10(np.maximum(image, 1e-6))
    base_density = -np.log10(np.maximum(base_rgb, 1e-6))
    dye_density = np.maximum(density - base_density, 0.0)
    if film_type == "bw-negative":
        gray = np.mean(dye_density, axis=2)
        region = content_region(gray[..., None].repeat(3, axis=2))[..., 0]
        low = float(np.percentile(region, 0.4))
        high = float(np.percentile(region, 99.7))
        normalized = np.clip((gray - low) / max(high - low, 1e-6), 0, 1)
        positive = np.repeat(normalized[..., None], 3, axis=2)
        return positive.astype(np.float32), {
            "mode": "density-inversion-bw",
            "base_density": base_density.tolist(),
            "density_range": [low, high],
        }

    region = content_region(dye_density).reshape(-1, 3)
    lows = np.percentile(region, 0.4, axis=0).astype(np.float32)
    highs = np.percentile(region, 99.7, axis=0).astype(np.float32)
    normalized = np.clip((dye_density - lows) / np.maximum(highs - lows, 1e-6), 0, 1)
    channel_gamma = np.asarray(profile.get("channel_gamma", [1, 1, 1]), dtype=np.float32)
    normalized = np.power(normalized, channel_gamma)
    matrix = np.asarray(profile.get("matrix", np.eye(3)), dtype=np.float32)
    positive = np.einsum("...c,dc->...d", normalized, matrix)
    positive = np.clip(positive, 0, 1)
    return positive.astype(np.float32), {
        "mode": "density-inversion-color",
        "base_density": base_density.tolist(),
        "density_lows": lows.tolist(),
        "density_highs": highs.tolist(),
        "channel_gamma": channel_gamma.tolist(),
        "matrix": matrix.tolist(),
    }


def apply_contrast(image: np.ndarray, amount: float) -> np.ndarray:
    return np.clip(0.5 + (image - 0.5) * amount, 0, 1)


def apply_filmic_curve(image: np.ndarray, toe: float, shoulder: float) -> np.ndarray:
    value = np.power(np.clip(image, 0, 1), 1.0 + max(toe, 0) * 1.5)
    value = 1.0 - (1.0 - value) / np.maximum(1.0 + max(shoulder, 0) * value, 1e-6)
    return np.clip(value, 0, 1)


def adjust_saturation_warmth(image: np.ndarray, saturation: float, warmth: float) -> np.ndarray:
    luma = rgb_luma(image)[..., None]
    result = luma + (image - luma) * saturation
    result[..., 0] += warmth
    result[..., 2] -= warmth * 0.8
    return np.clip(result, 0, 1)


def build_versions(positive: np.ndarray, profile: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    archive, archive_stretch = stretch_channels(positive, 0.05, 99.95)
    neutral, neutral_stretch = stretch_channels(positive, 0.35, 99.65)
    neutral = apply_filmic_curve(neutral, float(profile.get("toe", 0.03)) * 0.45, float(profile.get("shoulder", 0.12)) * 0.45)
    neutral = apply_contrast(neutral, float(profile.get("neutral_contrast", 1.0)))
    print_version = apply_filmic_curve(neutral, float(profile.get("toe", 0.03)), float(profile.get("shoulder", 0.12)))
    print_version = apply_contrast(print_version, float(profile.get("print_contrast", 1.1)))
    print_version = adjust_saturation_warmth(
        print_version,
        float(profile.get("print_saturation", 1.03)),
        float(profile.get("print_warmth", 0.0)),
    )
    return {
        "neutral": neutral.astype(np.float32),
        "print": print_version.astype(np.float32),
        "archive": archive.astype(np.float32),
    }, {
        "archive_stretch": archive_stretch,
        "neutral_stretch": neutral_stretch,
        "toe": profile.get("toe"),
        "shoulder": profile.get("shoulder"),
        "neutral_contrast": profile.get("neutral_contrast"),
        "print_contrast": profile.get("print_contrast"),
        "print_saturation": profile.get("print_saturation"),
        "print_warmth": profile.get("print_warmth"),
    }


def align_gray(reference: np.ndarray, moving: np.ndarray) -> tuple[np.ndarray, list[list[float]], float]:
    if moving.shape != reference.shape:
        moving = cv2.resize(moving, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    ref8 = np.clip(reference * 255, 0, 255).astype(np.uint8)
    mov8 = np.clip(moving * 255, 0, 255).astype(np.uint8)
    warp = np.eye(2, 3, dtype=np.float32)
    score = 0.0
    try:
        score, warp = cv2.findTransformECC(
            ref8,
            mov8,
            warp,
            cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5),
            None,
            3,
        )
        aligned = cv2.warpAffine(
            moving,
            warp,
            (reference.shape[1], reference.shape[0]),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REFLECT,
        )
    except cv2.error:
        aligned = moving
        warp = np.eye(2, 3, dtype=np.float32)
    return np.clip(aligned, 0, 1), warp.tolist(), float(score)


def robust_zscore(values: np.ndarray) -> np.ndarray:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return (values - median) / max(mad * 1.4826, 1e-6)


def detect_ir_defects(
    ir: np.ndarray,
    reference_luma: np.ndarray,
    polarity: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    aligned, warp, score = align_gray(reference_luma, ir)
    short = min(aligned.shape)
    kernel = max(5, int(short * 0.008) | 1)
    local = cv2.medianBlur(np.clip(aligned * 255, 0, 255).astype(np.uint8), kernel).astype(np.float32) / 255.0
    residual = aligned - local
    if polarity == "dark":
        z = -robust_zscore(residual)
    elif polarity == "bright":
        z = robust_zscore(residual)
    else:
        dark = -robust_zscore(residual)
        bright = robust_zscore(residual)
        dark_fraction = float(np.mean(dark > 5.0))
        bright_fraction = float(np.mean(bright > 5.0))
        if 0 < dark_fraction <= bright_fraction or bright_fraction == 0:
            z = dark
            polarity = "dark"
        else:
            z = bright
            polarity = "bright"
    mask = (z > 5.0).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    border = max(2, int(short * 0.09))
    mask[:border] = 0
    mask[-border:] = 0
    mask[:, :border] = 0
    mask[:, -border:] = 0
    gradient = cv2.magnitude(
        cv2.Sobel(reference_luma, cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(reference_luma, cv2.CV_32F, 0, 1, ksize=3),
    )
    overlap = float(np.mean(gradient[mask > 0] > np.percentile(gradient, 80))) if np.any(mask) else 0.0
    fraction = float(np.mean(mask > 0))
    conflict = fraction > 0.12 or overlap > 0.93
    if conflict:
        mask[:] = 0
    return mask, {
        "enabled": True,
        "alignment_warp": warp,
        "alignment_score": score,
        "polarity": polarity,
        "candidate_fraction": fraction,
        "edge_overlap": overlap,
        "conflict": conflict,
        "status": "rejected-conflict" if conflict else "accepted",
    }


def detect_visible_defects(image: np.ndarray, strength: str) -> tuple[np.ndarray, dict[str, Any]]:
    if strength == "off":
        return np.zeros(image.shape[:2], dtype=np.uint8), {"enabled": False, "candidate_fraction": 0.0}
    encoded = linear_to_srgb(image)
    gray = np.clip(rgb_luma(encoded) * 255, 0, 255).astype(np.uint8)
    short = min(gray.shape)
    kernel = max(5, int(short * 0.006) | 1)
    median = cv2.medianBlur(gray, kernel)
    residual = cv2.absdiff(gray, median).astype(np.float32)
    gradient = cv2.magnitude(
        cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3),
    )
    residual_z = robust_zscore(residual)
    gradient_limit = float(np.percentile(gradient, 55 if strength == "conservative" else 75))
    threshold = 8.5 if strength == "conservative" else 6.0
    mask = ((residual_z > threshold) & (gradient < gradient_limit)).astype(np.uint8)

    morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    top_hat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, morph_kernel).astype(np.float32)
    black_hat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, morph_kernel).astype(np.float32)
    sparse = np.maximum(top_hat, black_hat)
    sparse_z = robust_zscore(sparse)
    mask |= ((sparse_z > threshold + 1.0) & (gradient < gradient_limit * 1.15)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    border = max(2, int(short * 0.09))
    mask[:border] = 0
    mask[-border:] = 0
    mask[:, :border] = 0
    mask[:, -border:] = 0
    return mask, {
        "enabled": True,
        "strength": strength,
        "residual_threshold_mad": threshold,
        "candidate_fraction": float(np.mean(mask > 0)),
    }


def classify_defects(
    combined_mask: np.ndarray,
    ir_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    h, w = combined_mask.shape
    short = min(h, w)
    total = h * w
    scratch_seed = np.zeros_like(combined_mask, dtype=np.uint8)
    lines = cv2.HoughLinesP(
        (combined_mask > 0).astype(np.uint8) * 255,
        1,
        np.pi / 180,
        threshold=max(14, int(short * 0.025)),
        minLineLength=max(24, int(short * 0.12)),
        maxLineGap=max(6, int(short * 0.015)),
    )
    if lines is not None:
        line_width = max(3, int(short * 0.006))
        for line in lines[:, 0]:
            x1, y1, x2, y2 = [int(value) for value in line]
            cv2.line(scratch_seed, (x1, y1), (x2, y2), 1, line_width)
    enhanced_mask = np.maximum(combined_mask.astype(np.uint8), scratch_seed)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(enhanced_mask, connectivity=8)
    dust = np.zeros_like(combined_mask, dtype=np.uint8)
    scratches = np.zeros_like(combined_mask, dtype=np.uint8)
    candidates: list[dict[str, Any]] = []
    for label in range(1, count):
        x, y, bw, bh, area = [int(v) for v in stats[label]]
        if area < 2:
            continue
        long_side = max(bw, bh)
        short_side = max(1, min(bw, bh))
        aspect = long_side / short_side
        component = labels == label
        hough_overlap = float(np.mean(scratch_seed[component] > 0)) if np.any(component) else 0.0
        is_scratch = bool(
            (aspect >= 5.0 and long_side >= max(12, int(short * 0.018)))
            or area >= max(80, int(total * 0.00018))
            or hough_overlap > 0.12
        )
        source = "infrared" if np.mean(ir_mask[component] > 0) > 0.35 else "visible"
        confidence = float(np.clip(0.45 + min(area / max(total * 0.0002, 1), 0.25) + min(aspect / 20, 0.25), 0, 0.98))
        if is_scratch:
            scratches[component] = 1
            candidate_id = f"scratch-{len(candidates)+1:04d}"
            candidates.append({
                "id": candidate_id,
                "bbox": [x, y, bw, bh],
                "area": area,
                "aspect_ratio": aspect,
                "confidence": confidence,
                "source": source,
                "component_label": label,
            })
        else:
            dust[component] = 1
    return dust, scratches, candidates, labels


def load_review_decisions(path: Path | None) -> set[str]:
    if not path:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(item) for item in payload.get("approved_ids", [])}


def approved_scratch_mask(
    candidates: list[dict[str, Any]],
    labels: np.ndarray,
    approved_ids: set[str],
) -> np.ndarray:
    mask = np.zeros(labels.shape, dtype=np.uint8)
    for candidate in candidates:
        if candidate["id"] in approved_ids:
            mask[labels == int(candidate["component_label"])] = 1
    return mask


def repair_image(image: np.ndarray, mask: np.ndarray, radius: int = 3) -> np.ndarray:
    if not np.any(mask):
        return image.copy()
    encoded = linear_to_srgb(image)
    image8 = np.clip(np.round(encoded * 255), 0, 255).astype(np.uint8)
    repaired8 = cv2.inpaint(image8, (mask > 0).astype(np.uint8) * 255, radius, cv2.INPAINT_TELEA)
    repaired = srgb_to_linear(repaired8.astype(np.float32) / 255.0)
    output = image.copy()
    soft = cv2.GaussianBlur((mask > 0).astype(np.float32), (0, 0), 0.8)[..., None]
    output = output * (1.0 - soft) + repaired * soft
    return np.clip(output, 0, 1).astype(np.float32)


def grain_map(image: np.ndarray) -> tuple[np.ndarray, float]:
    luma = rgb_luma(linear_to_srgb(image))
    blur = cv2.GaussianBlur(luma, (0, 0), 1.2)
    high = luma - blur
    gradient = cv2.magnitude(
        cv2.Sobel(luma, cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(luma, cv2.CV_32F, 0, 1, ksize=3),
    )
    sample = (luma > 0.25) & (luma < 0.75) & (gradient < np.percentile(gradient, 35))
    sigma = float(np.std(high[sample])) if np.any(sample) else float(np.std(high))
    visualization = np.clip(0.5 + high / max(sigma * 6, 1e-4), 0, 1)
    return visualization.astype(np.float32), sigma


def process_grain(image: np.ndarray, mode: str, seed: int = 41041) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    visual, sigma = grain_map(image)
    if mode == "preserve":
        return image, visual, {"mode": mode, "estimated_sigma": sigma, "seed": None}
    encoded8 = np.clip(linear_to_srgb(image) * 255, 0, 255).astype(np.uint8)
    h_value = 3 if mode == "clean" else 4
    denoised8 = cv2.fastNlMeansDenoisingColored(encoded8, None, h_value, h_value, 7, 21)
    denoised = srgb_to_linear(denoised8.astype(np.float32) / 255.0)
    if mode == "clean":
        visual_clean, _ = grain_map(denoised)
        return denoised, visual_clean, {"mode": mode, "estimated_sigma": sigma, "denoise_h": h_value, "seed": None}
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 1.0, image.shape[:2]).astype(np.float32)
    noise -= cv2.GaussianBlur(noise, (0, 0), 0.75)
    noise /= max(float(np.std(noise)), 1e-6)
    luma = rgb_luma(denoised)
    density_weight = 0.55 + 0.45 * (1.0 - np.abs(luma - 0.5) * 2.0)
    amplitude = float(np.clip(sigma, 0.002, 0.018))
    rebuilt = np.clip(denoised + noise[..., None] * density_weight[..., None] * amplitude, 0, 1)
    rebuilt_visual, rebuilt_sigma = grain_map(rebuilt)
    return rebuilt.astype(np.float32), rebuilt_visual, {
        "mode": mode,
        "estimated_sigma": sigma,
        "rebuilt_sigma": rebuilt_sigma,
        "amplitude": amplitude,
        "denoise_h": h_value,
        "seed": seed,
    }


def save_edge_strip(path: Path, strip: np.ndarray) -> None:
    if strip.dtype == np.uint8:
        image8 = strip
    else:
        image8 = np.clip(linear_to_srgb(strip) * 255, 0, 255).astype(np.uint8)
    Image.fromarray(image8, "RGB").save(path)


def save_grain_visual(path: Path, visual: np.ndarray) -> None:
    Image.fromarray(np.clip(visual * 255, 0, 255).astype(np.uint8), "L").save(path)


def save_review_preview(path: Path, image: np.ndarray, max_width: int = 1800) -> tuple[int, int]:
    image8 = np.clip(linear_to_srgb(image) * 255, 0, 255).astype(np.uint8)
    preview = Image.fromarray(image8, "RGB")
    if preview.width > max_width:
        height = int(preview.height * max_width / preview.width)
        preview = preview.resize((max_width, height), Image.Resampling.LANCZOS)
    preview.save(path, quality=94, subsampling=0, icc_profile=SRGB_ICC)
    return preview.size


def write_review_html(
    path: Path,
    source_size: tuple[int, int],
    preview_size: tuple[int, int],
    candidates: list[dict[str, Any]],
) -> None:
    public_candidates = [{k: v for k, v in item.items() if k != "component_label"} for item in candidates]
    payload = json.dumps(public_candidates, ensure_ascii=False)
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Film Scan Lab - Scratch Review</title>
<style>
body{{margin:0;background:#0d1116;color:#eef1ed;font-family:Arial,'Microsoft YaHei',sans-serif}}main{{max-width:1280px;margin:auto;padding:28px}}
h1{{font-size:28px;margin:0 0 8px}}p{{color:#9ba5ab}}.viewer{{position:relative;border:1px solid #245b63;background:#070a0d}}
.viewer img{{display:block;width:100%;height:auto}}canvas{{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}}
.toolbar{{display:flex;gap:12px;align-items:center;margin:18px 0}}button{{background:#ff704c;color:#111;border:0;padding:10px 16px;font-weight:700;cursor:pointer}}
.items{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px}}label{{border:1px solid #27323a;padding:10px;background:#121820}}
code{{color:#42c2cf}}small{{color:#8f9aa1}}
</style></head><body><main><h1>大划痕人工确认</h1>
<p>勾选允许邻域修复的候选。确认不代表恢复了已经丢失的真实内容。</p>
<div class="viewer"><img id="preview" src="review-preview.jpg" alt="review preview"><canvas id="overlay"></canvas></div>
<div class="toolbar"><button id="download">下载 scratch-decisions.json</button><span><code>{len(candidates)}</code> 个候选</span></div>
<div id="items" class="items"></div></main>
<script>
const candidates={payload}; const source={{w:{source_size[0]},h:{source_size[1]}}};
const img=document.getElementById('preview'),canvas=document.getElementById('overlay'),ctx=canvas.getContext('2d'),items=document.getElementById('items');
function checked(){{return new Set([...document.querySelectorAll('input:checked')].map(x=>x.value))}}
function draw(){{canvas.width=img.clientWidth;canvas.height=img.clientHeight;ctx.clearRect(0,0,canvas.width,canvas.height);const sx=canvas.width/source.w,sy=canvas.height/source.h,sel=checked();
candidates.forEach(c=>{{const [x,y,w,h]=c.bbox;ctx.strokeStyle=sel.has(c.id)?'#ff704c':'#42c2cf';ctx.lineWidth=sel.has(c.id)?3:1.5;ctx.strokeRect(x*sx,y*sy,w*sx,h*sy);ctx.fillStyle=ctx.strokeStyle;ctx.fillText(c.id,x*sx+3,y*sy+13)}})}}
candidates.forEach(c=>{{const label=document.createElement('label');label.innerHTML=`<input type="checkbox" value="${{c.id}}"> <b>${{c.id}}</b><br><small>${{c.source}} · area ${{c.area}} · confidence ${{c.confidence.toFixed(2)}}</small>`;label.querySelector('input').addEventListener('change',draw);items.appendChild(label)}});
img.addEventListener('load',draw);window.addEventListener('resize',draw);
document.getElementById('download').onclick=()=>{{const data=JSON.stringify({{approved_ids:[...checked()]}},null,2);const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([data],{{type:'application/json'}}));a.download='scratch-decisions.json';a.click();URL.revokeObjectURL(a.href)}};
</script></body></html>"""
    path.write_text(document, encoding="utf-8")


def write_xmp(path: Path, settings: dict[str, Any]) -> None:
    x_ns = "adobe:ns:meta/"
    rdf_ns = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    crs_ns = "http://ns.adobe.com/camera-raw-settings/1.0/"
    ET.register_namespace("x", x_ns)
    ET.register_namespace("rdf", rdf_ns)
    ET.register_namespace("crs", crs_ns)
    root = ET.Element(f"{{{x_ns}}}xmpmeta")
    rdf = ET.SubElement(root, f"{{{rdf_ns}}}RDF")
    attributes = {f"{{{rdf_ns}}}about": ""}
    for key, value in settings.items():
        attributes[f"{{{crs_ns}}}{key}"] = str(value)
    ET.SubElement(rdf, f"{{{rdf_ns}}}Description", attributes)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    packet = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n' + xml + b'\n<?xpacket end="w"?>\n'
    path.write_bytes(packet)


def render_contact_sheet(path: Path, versions: list[tuple[str, np.ndarray]]) -> None:
    width = 1500
    cell_w = 460
    image_h = 330
    margin = 30
    canvas = Image.new("RGB", (width, 470), (13, 17, 22))
    draw = ImageDraw.Draw(canvas)
    try:
        title_font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 24)
        label_font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
    except OSError:
        title_font = ImageFont.load_default()
        label_font = ImageFont.load_default()
    draw.text((margin, 20), "FILM SCAN LAB / 三套输出", fill=(238, 241, 237), font=title_font)
    for index, (label, image) in enumerate(versions):
        encoded = np.clip(linear_to_srgb(image) * 255, 0, 255).astype(np.uint8)
        preview = Image.fromarray(encoded, "RGB")
        preview.thumbnail((cell_w, image_h), Image.Resampling.LANCZOS)
        x = margin + index * (cell_w + 20)
        y = 72 + (image_h - preview.height) // 2
        canvas.paste(preview, (x + (cell_w - preview.width) // 2, y))
        draw.rectangle((x, 72, x + cell_w, 72 + image_h), outline=(40, 101, 110), width=1)
        draw.text((x, 420), label, fill=(238, 241, 237), font=label_font)
    canvas.save(path, quality=94, subsampling=0, icc_profile=SRGB_ICC)


def html_table(rows: list[tuple[str, Any]]) -> str:
    return "".join(f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>" for key, value in rows)


def write_report(path: Path, analysis: dict[str, Any], outputs: list[str]) -> None:
    edge = analysis["edge_code"]
    base = analysis["base"]
    film = analysis["film"]
    defects = analysis["defects"]
    rows = [
        ("胶片类型", film["type"]),
        ("类型置信度", f"{film['confidence']:.2f}"),
        ("胶片配置", film["profile_name"]),
        ("边码", edge.get("text") or "未识别"),
        ("边码状态", edge.get("status")),
        ("片基方式", base["method"]),
        ("片基置信度", f"{base['confidence']:.2f}"),
        ("HDR", analysis["hdr"].get("enabled")),
        ("IR 状态", analysis["infrared"].get("status", "disabled")),
        ("灰尘像素比例", f"{defects['dust_fraction']*100:.3f}%"),
        ("大划痕候选", defects["scratch_candidate_count"]),
        ("颗粒模式", analysis["grain"]["mode"]),
    ]
    links = "".join(f'<li><a href="{html.escape(name)}">{html.escape(name)}</a></li>' for name in outputs)
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Film Scan Lab Report</title><style>body{{margin:0;background:#0d1116;color:#edf0ec;font-family:Arial,'Microsoft YaHei',sans-serif}}main{{max-width:1100px;margin:auto;padding:34px}}h1{{font-size:34px}}h2{{margin-top:34px;color:#45c0cc}}table{{width:100%;border-collapse:collapse;background:#121820}}th,td{{padding:12px;border-bottom:1px solid #27323a;text-align:left}}th{{width:240px;color:#9ba5ab}}a{{color:#ff8a5f}}img{{max-width:100%;border:1px solid #265d65}}.note{{border-left:4px solid #ff704c;padding:12px 16px;background:#141a21}}</style></head>
<body><main><h1>胶片扫描实验室</h1><p class="note">中性与印相版本属于数字解释。没有设备校准或色卡时，不把结果描述成绝对色彩还原。</p>
<img src="contact-sheet.jpg" alt="contact sheet"><h2>分析</h2><table>{html_table(rows)}</table>
<h2>缺陷确认</h2><p><a href="scratch-review.html">打开大划痕人工确认界面</a></p>
<h2>文件</h2><ul>{links}</ul></main></body></html>"""
    path.write_text(document, encoding="utf-8")


def validate_output(output_dir: Path) -> dict[str, Any]:
    required = [
        "00-source-master.tiff",
        "01-neutral-positive.tiff", "01-neutral-positive.jpg", "01-neutral-positive.xmp",
        "02-print-interpretation.tiff", "02-print-interpretation.jpg", "02-print-interpretation.xmp",
        "03-archive-master.tiff", "03-archive-master.jpg",
        "edge-code-strip.png", "edge-code.json",
        "dust-mask.png", "scratch-mask.png", "scratch-review.json", "scratch-review.html", "review-preview.jpg",
        "grain-map.png", "contact-sheet.jpg", "analysis.json", "parameters.json",
        "photoshop-adjustments.json", "report.html",
    ]
    files: dict[str, Any] = {}
    all_valid = True
    for name in required:
        path = output_dir / name
        exists = path.exists() and path.stat().st_size > 0
        files[name] = {"exists": exists, "bytes": path.stat().st_size if exists else 0}
        all_valid &= exists
    tiff_checks = []
    for name in ["00-source-master.tiff", "01-neutral-positive.tiff", "02-print-interpretation.tiff", "03-archive-master.tiff"]:
        path = output_dir / name
        valid = False
        shape = None
        dtype = None
        icc = False
        if path.exists():
            try:
                with tifffile.TiffFile(path) as tif:
                    page = tif.pages[0]
                    shape = list(page.shape)
                    dtype = str(page.dtype)
                    icc = bool(page.tags.get(34675))
                    valid = len(shape) == 3 and shape[2] == 3 and dtype == "uint16" and icc
            except Exception:
                valid = False
        all_valid &= valid
        tiff_checks.append({"file": name, "shape": shape, "dtype": dtype, "icc_profile": icc, "valid": valid})
    jpeg_checks = []
    for name in ["01-neutral-positive.jpg", "02-print-interpretation.jpg", "03-archive-master.jpg", "contact-sheet.jpg", "review-preview.jpg"]:
        valid = False
        icc = False
        path = output_dir / name
        if path.exists():
            try:
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    icc = bool(image.info.get("icc_profile"))
                valid = icc
            except Exception:
                valid = False
        all_valid &= valid
        jpeg_checks.append({"file": name, "icc_profile": icc, "valid": valid})
    xmp_checks = []
    for name in ["01-neutral-positive.xmp", "02-print-interpretation.xmp"]:
        valid = False
        try:
            content = (output_dir / name).read_bytes()
            start = content.find(b"<?xml")
            end = content.rfind(b"</x:xmpmeta>")
            ET.fromstring(content[start : end + len(b"</x:xmpmeta>")])
            valid = True
        except Exception:
            valid = False
        all_valid &= valid
        xmp_checks.append({"file": name, "valid": valid})
    return {
        "status": "pass" if all_valid else "fail",
        "files": files,
        "tiff_checks": tiff_checks,
        "jpeg_checks": jpeg_checks,
        "xmp_checks": xmp_checks,
    }


def default_xmp_settings(kind: str) -> dict[str, Any]:
    base = {
        "Version": "15.4",
        "ProcessVersion": "15.4",
        "WhiteBalance": "As Shot",
        "Temperature": 6500,
        "Tint": 0,
        "Exposure2012": "+0.00",
        "Contrast2012": 0,
        "Highlights2012": -8,
        "Shadows2012": 8,
        "Whites2012": 0,
        "Blacks2012": 0,
        "Texture": 0,
        "Clarity2012": 0,
        "Dehaze": 0,
        "Vibrance": 0,
        "Saturation": 0,
        "Sharpness": 0,
        "AlreadyApplied": "True",
    }
    if kind == "print":
        base.update({
            "Contrast2012": 8,
            "Highlights2012": -15,
            "Shadows2012": 4,
            "Whites2012": 3,
            "Blacks2012": -7,
            "Clarity2012": -2,
            "Vibrance": 5,
        })
    return base


def public_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in item.items() if k != "component_label"} for item in candidates]


def process_frame(
    input_path: Path,
    output_dir: Path,
    args: argparse.Namespace,
    shared_base: tuple[np.ndarray, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ensure_output_dir(output_dir, args.overwrite)
    capture_profiles = load_catalog(ASSETS_DIR / "capture-profiles.json", args.capture_profile_file)
    film_profiles = load_catalog(ASSETS_DIR / "film-profiles.json", args.film_profile_file)
    capture_profile = select_profile(capture_profiles, args.capture_profile)

    source_input, source_meta = read_image(input_path)
    source_linear = source_input if source_meta["input_linear"] else srgb_to_linear(source_input)
    capture_linear = apply_capture_profile(source_input, capture_profile, bool(source_meta["input_linear"]))

    hdr_images = [capture_linear]
    hdr_names = [input_path.name]
    for hdr_path in args.hdr:
        extra_path = Path(hdr_path)
        extra_input, extra_meta = read_image(extra_path)
        extra_linear = apply_capture_profile(extra_input, capture_profile, bool(extra_meta["input_linear"]))
        hdr_images.append(extra_linear)
        hdr_names.append(extra_path.name)
    working_scan, hdr_analysis = merge_hdr_scans(hdr_images)
    hdr_analysis["files"] = hdr_names

    edge_strip = make_edge_strip(working_scan)
    save_edge_strip(output_dir / "edge-code-strip.png", edge_strip)
    edge_result = ocr_edge_code(edge_strip, args.edge_code, args.tesseract_cmd)

    if shared_base:
        base_rgb = shared_base[0].copy()
        base_analysis = dict(shared_base[1])
        base_analysis["method"] = "shared-roll-base"
    else:
        base_rgb, base_analysis = estimate_base(working_scan, parse_roi(args.base_roi))

    inferred_type, type_analysis = infer_film_type(working_scan, base_rgb)
    if args.film_type != "auto":
        film_type = args.film_type
        type_analysis = {"method": "explicit", "confidence": 1.0, "automatic_candidate": inferred_type}
    else:
        film_type = inferred_type

    film_profile, profile_match = match_film_profile(
        film_profiles,
        edge_result.get("text", ""),
        args.film_profile,
        film_type,
    )
    if args.film_type == "auto" and profile_match["method"] == "edge-code":
        film_type = str(film_profile.get("type", film_type))
        type_analysis = {
            **type_analysis,
            "edge_code_override": True,
            "edge_code_type": film_type,
            "confidence": max(float(type_analysis.get("confidence", 0)), float(profile_match["confidence"])),
        }

    edge_payload = {
        **edge_result,
        "profile_match": profile_match,
        "selected_profile_id": film_profile["id"],
        "selected_profile_name": film_profile["name"],
    }
    write_json(output_dir / "edge-code.json", edge_payload)

    positive, inversion_analysis = invert_film(working_scan, film_type, base_rgb, film_profile)
    versions, tone_analysis = build_versions(positive, film_profile)

    visible_mask, visible_analysis = detect_visible_defects(versions["neutral"], args.dust)
    infrared_analysis: dict[str, Any] = {"enabled": False, "status": "disabled"}
    ir_mask = np.zeros(visible_mask.shape, dtype=np.uint8)
    if args.ir:
        ir_path = Path(args.ir)
        ir = read_gray(ir_path)
        ir_mask, infrared_analysis = detect_ir_defects(ir, rgb_luma(versions["neutral"]), args.ir_polarity)
        infrared_analysis["file"] = ir_path.name
    combined_mask = np.maximum(visible_mask, ir_mask)
    dust_mask, scratch_mask, candidates, component_labels = classify_defects(combined_mask, ir_mask)
    approved_ids = load_review_decisions(args.review_decisions)
    approved_mask = approved_scratch_mask(candidates, component_labels, approved_ids)

    archive_repaired = repair_image(versions["archive"], dust_mask, radius=2)
    working_repair_mask = np.maximum(dust_mask, approved_mask)
    neutral_repaired = repair_image(versions["neutral"], working_repair_mask, radius=3)
    print_repaired = repair_image(versions["print"], working_repair_mask, radius=3)
    neutral_final, grain_visual, grain_analysis = process_grain(neutral_repaired, args.grain)
    print_final, _, print_grain_analysis = process_grain(print_repaired, args.grain, seed=41042)

    write_png_mask(output_dir / "dust-mask.png", dust_mask)
    write_png_mask(output_dir / "scratch-mask.png", scratch_mask)
    save_grain_visual(output_dir / "grain-map.png", grain_visual)
    preview_size = save_review_preview(output_dir / "review-preview.jpg", versions["neutral"])
    review_payload = {
        "schema": "film-scan-lab/scratch-review/v1",
        "image": "review-preview.jpg",
        "source_size": [int(versions["neutral"].shape[1]), int(versions["neutral"].shape[0])],
        "preview_size": list(preview_size),
        "candidates": public_candidates(candidates),
        "approved_ids_applied": sorted(approved_ids),
    }
    write_json(output_dir / "scratch-review.json", review_payload)
    write_review_html(
        output_dir / "scratch-review.html",
        (versions["neutral"].shape[1], versions["neutral"].shape[0]),
        preview_size,
        candidates,
    )

    source_description = {
        "kind": "source-master",
        "source": input_path.name,
        "note": "Format-normalized source. The original user file remains unchanged.",
    }
    write_tiff(output_dir / "00-source-master.tiff", source_linear, source_description)
    outputs = {
        "neutral": ("01-neutral-positive", neutral_final),
        "print": ("02-print-interpretation", print_final),
        "archive": ("03-archive-master", archive_repaired),
    }
    for key, (stem, image) in outputs.items():
        description = {
            "kind": key,
            "film_type": film_type,
            "film_profile": film_profile["id"],
            "capture_profile": capture_profile["id"],
            "grain_mode": args.grain if key != "archive" else "preserve",
        }
        write_tiff(output_dir / f"{stem}.tiff", image, description)
        write_jpeg(output_dir / f"{stem}.jpg", image, args.jpeg_quality)

    neutral_xmp = default_xmp_settings("neutral")
    print_xmp = default_xmp_settings("print")
    write_xmp(output_dir / "01-neutral-positive.xmp", neutral_xmp)
    write_xmp(output_dir / "02-print-interpretation.xmp", print_xmp)

    defects_analysis = {
        "visible": visible_analysis,
        "dust_fraction": float(np.mean(dust_mask > 0)),
        "scratch_fraction": float(np.mean(scratch_mask > 0)),
        "scratch_candidate_count": len(candidates),
        "approved_ids": sorted(approved_ids),
        "repair_fraction": float(np.mean(working_repair_mask > 0)),
    }
    film_analysis = {
        "type": film_type,
        "confidence": float(type_analysis.get("confidence", 0)),
        "type_analysis": type_analysis,
        "profile_id": film_profile["id"],
        "profile_name": film_profile["name"],
        "profile_match": profile_match,
    }
    analysis = {
        "schema": "film-scan-lab/analysis/v1",
        "source": {
            "name": input_path.name,
            "extension": input_path.suffix.lower(),
            "bytes": input_path.stat().st_size,
            "sha256": sha256_file(input_path),
            **source_meta,
        },
        "capture": {
            "profile_id": capture_profile["id"],
            "profile_name": capture_profile["name"],
            "confidence": "user-selected" if args.capture_profile != "generic-scanner-srgb" else "generic",
        },
        "film": film_analysis,
        "edge_code": edge_payload,
        "base": base_analysis,
        "hdr": hdr_analysis,
        "infrared": infrared_analysis,
        "inversion": inversion_analysis,
        "tone": tone_analysis,
        "defects": defects_analysis,
        "grain": grain_analysis,
        "limitations": [
            "Film-stock profiles are practical starting points, not manufacturer spectral models.",
            "XMP sidecars apply to rendered positive files and do not reproduce density-space inversion.",
            "Large scratch approval permits neighborhood repair; it does not reconstruct ground truth.",
        ],
    }
    parameters = {
        "schema": "film-scan-lab/parameters/v1",
        "capture_profile": capture_profile,
        "film_profile": film_profile,
        "base": base_analysis,
        "inversion": inversion_analysis,
        "tone": tone_analysis,
        "dust_mode": args.dust,
        "grain": grain_analysis,
        "print_grain": print_grain_analysis,
        "approved_scratch_ids": sorted(approved_ids),
        "xmp": {"neutral": neutral_xmp, "print": print_xmp},
    }
    photoshop = {
        "schema": "film-scan-lab/photoshop-adjustments/v1",
        "applies_to": "rendered positive TIFF/JPEG",
        "neutral": {
            "curve_points": [[0, 0], [32, 28], [128, 130], [224, 228], [255, 255]],
            "color_balance": {"shadows": [0, 0, 0], "midtones": [0, 0, 0], "highlights": [0, 0, 0]},
        },
        "print": {
            "curve_points": [[0, 0], [32, 24], [128, 132], [224, 235], [255, 255]],
            "color_balance": {"shadows": [0, 0, 0], "midtones": [2, 0, -2], "highlights": [3, 1, -3]},
        },
        "masks": {"dust": "dust-mask.png", "scratch": "scratch-mask.png"},
        "note": "Basic Photoshop/ACR parameters cannot reproduce film-base density subtraction.",
    }
    write_json(output_dir / "analysis.json", analysis)
    write_json(output_dir / "parameters.json", parameters)
    write_json(output_dir / "photoshop-adjustments.json", photoshop)
    render_contact_sheet(
        output_dir / "contact-sheet.jpg",
        [("中性正片 / Neutral", neutral_final), ("印相解释 / Print", print_final), ("档案正片 / Archive", archive_repaired)],
    )

    report_files = [
        "00-source-master.tiff",
        "01-neutral-positive.tiff", "01-neutral-positive.jpg", "01-neutral-positive.xmp",
        "02-print-interpretation.tiff", "02-print-interpretation.jpg", "02-print-interpretation.xmp",
        "03-archive-master.tiff", "03-archive-master.jpg",
        "edge-code-strip.png", "edge-code.json", "dust-mask.png", "scratch-mask.png",
        "scratch-review.json", "grain-map.png", "analysis.json", "parameters.json",
        "photoshop-adjustments.json",
    ]
    write_report(output_dir / "report.html", analysis, report_files)
    validation = validate_output(output_dir)
    write_json(output_dir / "validation.json", validation)
    return {
        "input": input_path.name,
        "output_dir": str(output_dir),
        "status": validation["status"],
        "neutral_jpeg": str(output_dir / "01-neutral-positive.jpg"),
        "base_rgb": base_rgb.tolist(),
        "base_analysis": base_analysis,
        "film_type": film_type,
        "film_profile": film_profile["id"],
        "scratch_candidates": len(candidates),
    }


def render_batch_contact_sheet(path: Path, items: list[tuple[str, Path]]) -> None:
    columns = 4
    cell_w, cell_h = 340, 280
    margin, gap = 24, 18
    rows = math.ceil(len(items) / columns)
    canvas = Image.new("RGB", (margin * 2 + columns * cell_w + (columns - 1) * gap, margin * 2 + rows * cell_h + (rows - 1) * gap), (13, 17, 22))
    draw = ImageDraw.Draw(canvas)
    try:
        label_font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 17)
    except OSError:
        label_font = ImageFont.load_default()
    for index, (label, image_path) in enumerate(items):
        row, col = divmod(index, columns)
        x = margin + col * (cell_w + gap)
        y = margin + row * (cell_h + gap)
        with Image.open(image_path) as opened:
            preview = opened.convert("RGB")
            preview.thumbnail((cell_w, cell_h - 35), Image.Resampling.LANCZOS)
        canvas.paste(preview, (x + (cell_w - preview.width) // 2, y))
        draw.text((x, y + cell_h - 28), label, font=label_font, fill=(238, 241, 237))
    canvas.save(path, quality=92, subsampling=0, icc_profile=SRGB_ICC)


def process_batch(input_dir: Path, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    if args.hdr or args.ir:
        raise FilmScanError("Directory mode does not assign HDR/IR files automatically; process those frames individually.")
    ensure_output_dir(output_dir, args.overwrite)
    files = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not files:
        raise FilmScanError("No supported film scan files found in the input directory")

    capture_profiles = load_catalog(ASSETS_DIR / "capture-profiles.json", args.capture_profile_file)
    capture_profile = select_profile(capture_profiles, args.capture_profile)
    base_values = []
    base_records = []
    roi = parse_roi(args.base_roi)
    for path in files:
        source, meta = read_image(path)
        linear = apply_capture_profile(source, capture_profile, bool(meta["input_linear"]))
        base, record = estimate_base(linear, roi)
        base_values.append(base)
        base_records.append(record)
    shared_rgb = np.median(np.stack(base_values), axis=0).astype(np.float32)
    shared_record = {
        "method": "shared-roll-base",
        "base_rgb_linear": shared_rgb.tolist(),
        "base_density": (-np.log10(np.maximum(shared_rgb, 1e-6))).tolist(),
        "confidence": float(np.median([record["confidence"] for record in base_records])),
        "frame_count": len(files),
        "per_frame_candidates": [record["base_rgb_linear"] for record in base_records],
        "roi": list(roi) if roi else None,
    }

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    results = []
    contact_items = []
    for index, path in enumerate(files, start=1):
        frame_dir = frames_dir / f"{index:03d}-{path.stem}"
        frame_args = argparse.Namespace(**vars(args))
        frame_args.overwrite = False
        result = process_frame(path, frame_dir, frame_args, (shared_rgb, shared_record))
        results.append(result)
        contact_items.append((path.name, frame_dir / "01-neutral-positive.jpg"))
    render_batch_contact_sheet(output_dir / "batch-contact-sheet.jpg", contact_items)
    status = "pass" if all(item["status"] == "pass" for item in results) else "fail"
    payload = {
        "schema": "film-scan-lab/batch/v1",
        "status": status,
        "frame_count": len(results),
        "shared_base": shared_record,
        "frames": results,
    }
    write_json(output_dir / "batch-analysis.json", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Film Scan Lab: invert, restore, review, and archive film scans")
    parser.add_argument("input", type=Path, help="Film scan file or a directory containing one roll")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--film-type", choices=["auto", "color-negative", "bw-negative", "slide"], default="auto")
    parser.add_argument("--capture-profile", default="generic-scanner-srgb")
    parser.add_argument("--capture-profile-file", type=Path)
    parser.add_argument("--film-profile")
    parser.add_argument("--film-profile-file", type=Path)
    parser.add_argument("--base-roi", help="Film-base ROI as x,y,width,height")
    parser.add_argument("--hdr", nargs="*", default=[], help="Additional scans of the same frame at different exposures")
    parser.add_argument("--ir", type=Path, help="Optional infrared dust scan")
    parser.add_argument("--ir-polarity", choices=["auto", "dark", "bright"], default="auto")
    parser.add_argument("--edge-code", help="Manual film edge code when OCR is unavailable or uncertain")
    parser.add_argument("--tesseract-cmd", help="Path to a Tesseract executable")
    parser.add_argument("--dust", choices=["conservative", "normal", "off"], default="conservative")
    parser.add_argument("--grain", choices=["preserve", "clean", "rebuild"], default="preserve")
    parser.add_argument("--review-decisions", type=Path, help="JSON downloaded from scratch-review.html")
    parser.add_argument("--jpeg-quality", type=int, default=94)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if not args.input.exists():
            raise FilmScanError(f"Input does not exist: {args.input}")
        if args.input.is_dir():
            result = process_batch(args.input, args.output_dir, args)
        else:
            if args.input.suffix.lower() not in SUPPORTED_EXTENSIONS:
                raise FilmScanError(f"Unsupported input extension: {args.input.suffix}")
            result = process_frame(args.input, args.output_dir, args)
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "pass" else 2
    except FilmScanError as error:
        print(f"Film Scan Lab error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
