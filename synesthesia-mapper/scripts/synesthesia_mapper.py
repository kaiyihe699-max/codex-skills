#!/usr/bin/env python3
"""Deterministic image-to-MIDI/WAV/visual-score mapper."""

from __future__ import annotations

import argparse
import base64
import colorsys
import hashlib
import html
import io
import json
import math
import struct
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PRESET_DIR = SKILL_DIR / "assets" / "presets"

ROOTS = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}

SCALES = {
    "pentatonic": [0, 2, 4, 7, 9],
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "chromatic": list(range(12)),
}

TIMBRES = ("glass", "warm", "grain", "pulse")
MIDI_PROGRAMS = (10, 48, 12, 81)


@dataclass
class NoteEvent:
    start_beat: float
    duration_beats: float
    midi_note: int
    velocity: int
    timbre: str
    timbre_index: int
    source_rgb: list[int]
    source_hsv: list[float]
    source_position: list[float]
    texture: float
    salience: float


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_preset(name_or_path: str) -> dict[str, Any]:
    supplied = Path(name_or_path)
    path = supplied if supplied.is_file() else PRESET_DIR / f"{name_or_path}.json"
    if not path.is_file():
        available = ", ".join(sorted(item.stem for item in PRESET_DIR.glob("*.json")))
        raise ValueError(f"找不到预设 {name_or_path!r}。可用预设：{available}")
    preset = load_json(path)
    preset["_path"] = str(path)
    return preset


def load_image(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
    background = Image.new("RGBA", image.size, (8, 12, 18, 255))
    background.alpha_composite(image)
    return background.convert("RGB")


def resize_for_analysis(image: Image.Image, size: int = 384) -> np.ndarray:
    fitted = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
    return np.asarray(fitted, dtype=np.float32) / 255.0


def rgb_to_hsv_array(rgb: np.ndarray) -> np.ndarray:
    flat = rgb.reshape(-1, 3)
    hsv = np.array([colorsys.rgb_to_hsv(*pixel) for pixel in flat], dtype=np.float32)
    return hsv.reshape((*rgb.shape[:2], 3))


def texture_map(rgb: np.ndarray) -> np.ndarray:
    luminance = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    gx = np.abs(np.diff(luminance, axis=1, prepend=luminance[:, :1]))
    gy = np.abs(np.diff(luminance, axis=0, prepend=luminance[:1, :]))
    return np.clip((gx + gy) * 2.4, 0.0, 1.0)


def cell_masks(size: int, steps: int, bands: int, scan: str) -> Iterable[tuple[int, int, np.ndarray]]:
    yy, xx = np.mgrid[0:size, 0:size]
    if scan == "horizontal":
        x_edges = np.linspace(0, size, steps + 1, dtype=int)
        y_edges = np.linspace(0, size, bands + 1, dtype=int)
        for step in range(steps):
            for band in range(bands):
                mask = np.zeros((size, size), dtype=bool)
                mask[y_edges[band] : y_edges[band + 1], x_edges[step] : x_edges[step + 1]] = True
                yield step, band, mask
        return

    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    dx = xx - cx
    dy = yy - cy
    radius = np.sqrt(dx * dx + dy * dy) / (size * math.sqrt(2) / 2.0)
    angle = (np.arctan2(dy, dx) + math.pi / 2.0) % (2.0 * math.pi)
    time_bin = np.floor(angle / (2.0 * math.pi) * steps).astype(int)
    ring_bin = np.floor(np.clip(radius, 0, 0.999999) * bands).astype(int)
    for step in range(steps):
        for band in range(bands):
            yield step, band, (time_bin == step) & (ring_bin == band) & (radius <= 1.0)


def describe_cells(rgb: np.ndarray, steps: int, bands: int, scan: str) -> list[dict[str, Any]]:
    hsv = rgb_to_hsv_array(rgb)
    texture = texture_map(rgb)
    cells: list[dict[str, Any]] = []
    for step, band, mask in cell_masks(rgb.shape[0], steps, bands, scan):
        if not np.any(mask):
            continue
        rgb_mean = np.mean(rgb[mask], axis=0)
        hsv_mean = np.mean(hsv[mask], axis=0)
        texture_mean = float(np.mean(texture[mask]))
        luminance = float(np.dot(rgb_mean, np.array([0.2126, 0.7152, 0.0722])))
        contrast = float(np.std(rgb[mask]))
        salience = (
            float(hsv_mean[1]) * 0.40
            + texture_mean * 0.30
            + contrast * 0.18
            + abs(luminance - 0.5) * 0.12
        )
        cells.append(
            {
                "step": step,
                "band": band,
                "rgb": rgb_mean,
                "hsv": hsv_mean,
                "texture": texture_mean,
                "luminance": luminance,
                "contrast": contrast,
                "salience": salience,
            }
        )
    return cells


def scale_notes(root: str, scale: str, low: int, high: int) -> list[int]:
    root_pc = ROOTS[root.upper()]
    intervals = SCALES[scale]
    return [note for note in range(low, high + 1) if (note - root_pc) % 12 in intervals]


def hue_to_timbre(hue: float) -> int:
    if hue < 0.09 or hue >= 0.91:
        return 1
    if hue < 0.30:
        return 2
    if hue < 0.58:
        return 0
    return 3


def map_notes(cells: list[dict[str, Any]], settings: dict[str, Any]) -> list[NoteEvent]:
    steps = settings["steps"]
    bands = settings["bands"]
    notes = scale_notes(settings["root"], settings["scale"], settings["min_midi"], settings["max_midi"])
    by_step: dict[int, list[dict[str, Any]]] = {index: [] for index in range(steps)}
    for cell in cells:
        by_step[cell["step"]].append(cell)

    events: list[NoteEvent] = []
    max_per_step = max(1, int(round(1 + settings["density"] * 2.5)))
    threshold = 0.10 + (1.0 - settings["density"]) * 0.18
    step_beats = settings["beats_per_bar"] * settings["bars"] / steps

    for step, candidates in by_step.items():
        chosen = sorted(candidates, key=lambda item: item["salience"], reverse=True)
        chosen = [item for item in chosen if item["salience"] >= threshold][:max_per_step]
        if not chosen and candidates and step % 4 == 0:
            chosen = [max(candidates, key=lambda item: item["salience"])]

        occupied: set[int] = set()
        for cell in chosen:
            vertical = 1.0 - cell["band"] / max(1, bands - 1)
            pitch_index = int(round(vertical * (len(notes) - 1)))
            midi_note = notes[pitch_index]
            while midi_note in occupied and midi_note + 12 <= settings["max_midi"]:
                midi_note += 12
            occupied.add(midi_note)

            hue, saturation, value = (float(component) for component in cell["hsv"])
            timbre_index = hue_to_timbre(hue)
            velocity = int(round(clamp(38 + saturation * 49 + cell["texture"] * 28 + value * 10, 35, 120)))
            duration_steps = 1 + int(cell["luminance"] > 0.48) + int(cell["contrast"] < 0.20)
            duration_beats = step_beats * duration_steps * settings["smoothness"]
            duration_beats = clamp(duration_beats, step_beats * 0.55, step_beats * 3.2)
            rgb_255 = [int(round(clamp(float(value), 0, 1) * 255)) for value in cell["rgb"]]
            events.append(
                NoteEvent(
                    start_beat=round(step * step_beats, 6),
                    duration_beats=round(duration_beats, 6),
                    midi_note=midi_note,
                    velocity=velocity,
                    timbre=TIMBRES[timbre_index],
                    timbre_index=timbre_index,
                    source_rgb=rgb_255,
                    source_hsv=[round(hue, 5), round(saturation, 5), round(value, 5)],
                    source_position=[round(step / max(1, steps - 1), 5), round(cell["band"] / max(1, bands - 1), 5)],
                    texture=round(cell["texture"], 5),
                    salience=round(cell["salience"], 5),
                )
            )
    return sorted(events, key=lambda event: (event.start_beat, event.midi_note))


def variable_length(value: int) -> bytes:
    buffer = value & 0x7F
    result = bytearray([buffer])
    while value >> 7:
        value >>= 7
        buffer = (value & 0x7F) | 0x80
        result.insert(0, buffer)
    return bytes(result)


def write_midi(path: Path, events: list[NoteEvent], bpm: int, title: str, ticks_per_beat: int = 480) -> None:
    track_events: list[tuple[int, int, bytes]] = []
    title_bytes = title.encode("utf-8")[:120]
    track_events.append((0, 0, b"\xFF\x03" + variable_length(len(title_bytes)) + title_bytes))
    tempo = int(round(60_000_000 / bpm))
    track_events.append((0, 0, b"\xFF\x51\x03" + tempo.to_bytes(3, "big")))
    track_events.append((0, 0, b"\xFF\x58\x04\x04\x02\x18\x08"))
    for channel, program in enumerate(MIDI_PROGRAMS):
        track_events.append((0, 1, bytes([0xC0 | channel, program])))
    for event in events:
        channel = event.timbre_index
        start = int(round(event.start_beat * ticks_per_beat))
        end = max(start + 1, int(round((event.start_beat + event.duration_beats) * ticks_per_beat)))
        track_events.append((start, 3, bytes([0x90 | channel, event.midi_note, event.velocity])))
        track_events.append((end, 2, bytes([0x80 | channel, event.midi_note, 0])))
    track_events.sort(key=lambda item: (item[0], item[1]))

    track = bytearray()
    previous = 0
    for tick, _, message in track_events:
        track.extend(variable_length(tick - previous))
        track.extend(message)
        previous = tick
    track.extend(b"\x00\xFF\x2F\x00")

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, ticks_per_beat)
    chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    path.write_bytes(header + chunk)


def oscillator(phase: np.ndarray, timbre: int, rng: np.random.Generator) -> np.ndarray:
    if timbre == 0:
        return 0.78 * np.sin(phase) + 0.22 * np.sin(phase * 2.01)
    if timbre == 1:
        return (2.0 / math.pi) * np.arcsin(np.sin(phase))
    if timbre == 2:
        harmonics = sum(np.sin(phase * index) / index for index in range(1, 6))
        return harmonics / 1.83
    noise = rng.normal(0.0, 0.10, phase.shape[0])
    return 0.62 * np.sin(phase) + 0.28 * np.sin(phase * 3.0) + noise


def envelope(length: int, sample_rate: int) -> np.ndarray:
    attack = min(length // 4, int(sample_rate * 0.018))
    release = min(length // 3, int(sample_rate * 0.20))
    sustain = max(0, length - attack - release)
    parts = []
    if attack:
        parts.append(np.linspace(0.0, 1.0, attack, endpoint=False, dtype=np.float32))
    if sustain:
        parts.append(np.linspace(1.0, 0.72, sustain, endpoint=False, dtype=np.float32))
    if release:
        parts.append(np.linspace(0.72, 0.0, release, endpoint=True, dtype=np.float32))
    return np.concatenate(parts) if parts else np.zeros(length, dtype=np.float32)


def write_wav(
    path: Path,
    events: list[NoteEvent],
    bpm: int,
    total_beats: float,
    sample_rate: int,
    seed: int,
) -> dict[str, float]:
    seconds_per_beat = 60.0 / bpm
    duration_seconds = total_beats * seconds_per_beat + 0.8
    sample_count = int(math.ceil(duration_seconds * sample_rate))
    mix = np.zeros((sample_count, 2), dtype=np.float32)
    rng = np.random.default_rng(seed)

    for event in events:
        start = int(event.start_beat * seconds_per_beat * sample_rate)
        duration = event.duration_beats * seconds_per_beat + 0.20
        length = min(int(duration * sample_rate), sample_count - start)
        if length <= 0:
            continue
        frequency = 440.0 * (2.0 ** ((event.midi_note - 69) / 12.0))
        time = np.arange(length, dtype=np.float32) / sample_rate
        phase = 2.0 * math.pi * frequency * time
        signal = oscillator(phase, event.timbre_index, rng).astype(np.float32)
        signal *= envelope(length, sample_rate)
        signal *= event.velocity / 127.0 * 0.17
        pan = clamp(event.source_position[0], 0.05, 0.95)
        left = math.cos(pan * math.pi / 2.0)
        right = math.sin(pan * math.pi / 2.0)
        mix[start : start + length, 0] += signal * left
        mix[start : start + length, 1] += signal * right

    raw_peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if raw_peak > 0:
        mix = np.tanh(mix * 1.15)
        peak = float(np.max(np.abs(mix)))
        mix *= 0.92 / max(peak, 1e-8)
    pcm = np.clip(mix * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    return {"duration_seconds": round(duration_seconds, 4), "raw_peak": round(raw_peak, 6)}


def find_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for name in names:
        if Path(name).is_file():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def note_color(event: NoteEvent) -> tuple[int, int, int]:
    rgb = np.array(event.source_rgb, dtype=np.float32)
    rgb = rgb * 0.82 + np.array([32, 38, 46], dtype=np.float32) * 0.18
    return tuple(int(clamp(component, 38, 245)) for component in rgb)


def render_score_png(
    path: Path,
    image: Image.Image,
    events: list[NoteEvent],
    settings: dict[str, Any],
    title: str,
) -> None:
    width, height = 1600, 1000
    canvas = Image.new("RGB", (width, height), "#091119")
    draw = ImageDraw.Draw(canvas)
    title_font = find_font(48, True)
    meta_font = find_font(22)
    label_font = find_font(18)
    draw.text((72, 52), title, font=title_font, fill="#F4F6F8")
    meta = f"{settings['root']} {settings['scale']}  |  {settings['bpm']} BPM  |  {settings['scan']} scan  |  {len(events)} notes"
    draw.text((74, 118), meta, font=meta_font, fill="#95A6B5")

    thumb = ImageOps.fit(image, (370, 370), method=Image.Resampling.LANCZOS)
    canvas.paste(thumb, (72, 190))
    draw.rounded_rectangle((72, 190, 442, 560), radius=8, outline="#51C3D7", width=2)
    draw.text((72, 585), "SOURCE IMAGE", font=label_font, fill="#6FD7E8")
    draw.text((72, 625), "亮度 → 音区 / 时值", font=meta_font, fill="#D1DAE1")
    draw.text((72, 664), "色相 → 音色", font=meta_font, fill="#D1DAE1")
    draw.text((72, 703), "纵向位置 → 音高", font=meta_font, fill="#D1DAE1")
    draw.text((72, 742), "纹理 → 音符密度", font=meta_font, fill="#D1DAE1")

    x0, y0, x1, y1 = 500, 190, 1528, 850
    draw.rectangle((x0, y0, x1, y1), fill="#0D1822", outline="#243644", width=2)
    total_beats = settings["bars"] * settings["beats_per_bar"]
    min_note, max_note = settings["min_midi"], settings["max_midi"]
    for bar in range(settings["bars"] + 1):
        x = x0 + (x1 - x0) * bar / settings["bars"]
        draw.line((x, y0, x, y1), fill="#2D4350", width=2)
        if bar < settings["bars"]:
            draw.text((x + 8, y0 + 8), f"{bar + 1:02d}", font=label_font, fill="#617987")
    for note in range(min_note, max_note + 1):
        if note % 12 == ROOTS[settings["root"].upper()]:
            y = y1 - (note - min_note) / max(1, max_note - min_note) * (y1 - y0)
            draw.line((x0, y, x1, y), fill="#21323D", width=1)

    for event in events:
        ex0 = x0 + event.start_beat / total_beats * (x1 - x0)
        ex1 = x0 + (event.start_beat + event.duration_beats) / total_beats * (x1 - x0)
        ey = y1 - (event.midi_note - min_note) / max(1, max_note - min_note) * (y1 - y0)
        note_height = 8 + event.velocity / 127.0 * 8
        draw.rounded_rectangle(
            (ex0, ey - note_height / 2, max(ex0 + 4, ex1), ey + note_height / 2),
            radius=3,
            fill=note_color(event),
        )

    draw.text((500, 875), "TIME →", font=meta_font, fill="#95A6B5")
    draw.text((72, 905), "共感谱  /  deterministic visual-to-sound mapping", font=meta_font, fill="#566B79")
    canvas.save(path, quality=95)


def image_data_url(image: Image.Image) -> str:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def render_score_svg(
    path: Path,
    image: Image.Image,
    events: list[NoteEvent],
    settings: dict[str, Any],
    title: str,
) -> None:
    width, height = 1600, 1000
    x0, y0, x1, y1 = 500, 190, 1528, 850
    total_beats = settings["bars"] * settings["beats_per_bar"]
    min_note, max_note = settings["min_midi"], settings["max_midi"]
    thumb = ImageOps.fit(image, (370, 370), method=Image.Resampling.LANCZOS)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="1600" height="1000" fill="#091119"/>',
        f'<text x="72" y="98" fill="#F4F6F8" font-size="48" font-weight="700" font-family="Arial, sans-serif">{html.escape(title)}</text>',
        f'<text x="74" y="142" fill="#95A6B5" font-size="22" font-family="Arial, sans-serif">{html.escape(settings["root"] + " " + settings["scale"] + " | " + str(settings["bpm"]) + " BPM | " + settings["scan"] + " scan")}</text>',
        f'<image x="72" y="190" width="370" height="370" href="{image_data_url(thumb)}"/>',
        '<rect x="72" y="190" width="370" height="370" rx="8" fill="none" stroke="#51C3D7" stroke-width="2"/>',
        f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" fill="#0D1822" stroke="#243644" stroke-width="2"/>',
    ]
    for bar in range(settings["bars"] + 1):
        x = x0 + (x1 - x0) * bar / settings["bars"]
        parts.append(f'<line x1="{x:.2f}" y1="{y0}" x2="{x:.2f}" y2="{y1}" stroke="#2D4350" stroke-width="2"/>')
    for event in events:
        ex0 = x0 + event.start_beat / total_beats * (x1 - x0)
        ex1 = x0 + (event.start_beat + event.duration_beats) / total_beats * (x1 - x0)
        ey = y1 - (event.midi_note - min_note) / max(1, max_note - min_note) * (y1 - y0)
        note_height = 8 + event.velocity / 127.0 * 8
        color = "#%02x%02x%02x" % note_color(event)
        parts.append(f'<rect x="{ex0:.2f}" y="{ey-note_height/2:.2f}" width="{max(4, ex1-ex0):.2f}" height="{note_height:.2f}" rx="3" fill="{color}"/>')
    parts.extend(
        [
            '<text x="72" y="610" fill="#6FD7E8" font-size="18" font-family="Arial, sans-serif">SOURCE IMAGE</text>',
            '<text x="72" y="660" fill="#D1DAE1" font-size="22" font-family="Arial, sans-serif">brightness → register / duration</text>',
            '<text x="72" y="700" fill="#D1DAE1" font-size="22" font-family="Arial, sans-serif">hue → timbre</text>',
            '<text x="72" y="740" fill="#D1DAE1" font-size="22" font-family="Arial, sans-serif">vertical position → pitch</text>',
            '<text x="72" y="780" fill="#D1DAE1" font-size="22" font-family="Arial, sans-serif">texture → note density</text>',
            '<text x="72" y="930" fill="#566B79" font-size="22" font-family="Arial, sans-serif">Synesthesia Mapper / deterministic visual-to-sound mapping</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(parts), encoding="utf-8")


def render_preview_html(
    path: Path,
    source_name: str,
    events: list[NoteEvent],
    settings: dict[str, Any],
    title: str,
    duration_seconds: float,
) -> None:
    total_beats = settings["bars"] * settings["beats_per_bar"]
    min_note, max_note = settings["min_midi"], settings["max_midi"]
    note_markup = []
    for index, event in enumerate(events):
        left = event.start_beat / total_beats * 100.0
        width = max(0.35, event.duration_beats / total_beats * 100.0)
        bottom = (event.midi_note - min_note) / max(1, max_note - min_note) * 94.0
        color = "#%02x%02x%02x" % note_color(event)
        start_seconds = event.start_beat * 60.0 / settings["bpm"]
        end_seconds = (event.start_beat + event.duration_beats) * 60.0 / settings["bpm"]
        note_markup.append(
            f'<span class="note" data-start="{start_seconds:.4f}" data-end="{end_seconds:.4f}" '
            f'style="left:{left:.4f}%;width:{width:.4f}%;bottom:{bottom:.4f}%;background:{color}" title="MIDI {event.midi_note} · {event.timbre}"></span>'
        )
    event_json = json.dumps([asdict(event) for event in events], ensure_ascii=False).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · 共感谱</title>
<style>
:root{{--bg:#071019;--panel:#0d1923;--line:#29404d;--text:#f1f5f7;--muted:#91a5b2;--cyan:#66d5e6;--orange:#ffac63}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font-family:"Microsoft YaHei",Inter,Arial,sans-serif}}
main{{width:min(1180px,calc(100% - 40px));margin:42px auto 70px}} header{{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:28px}}
h1{{font-size:clamp(30px,5vw,58px);line-height:1.08;margin:0 0 8px;letter-spacing:0}} p{{margin:0;color:var(--muted);line-height:1.65}}
.tag{{color:var(--cyan);font-size:13px;letter-spacing:0;text-transform:uppercase}} .grid{{display:grid;grid-template-columns:minmax(280px,420px) 1fr;gap:22px}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}} .source{{position:relative;aspect-ratio:1;background:#03070b;cursor:crosshair}}
.source img{{width:100%;height:100%;object-fit:cover;display:block}} .scan{{position:absolute;top:0;bottom:0;left:0;width:2px;background:var(--orange);box-shadow:0 0 18px #ff9d4d;pointer-events:none}}
.info{{padding:18px 20px;display:grid;grid-template-columns:repeat(2,1fr);gap:12px}} .info b{{display:block;font-size:18px}} .info span{{color:var(--muted);font-size:12px}}
.score{{position:relative;min-height:520px;background:repeating-linear-gradient(90deg,transparent 0,transparent calc(12.5% - 1px),#20333f calc(12.5% - 1px),#20333f 12.5%),#09131c}}
.note{{position:absolute;height:9px;border-radius:3px;opacity:.80;transition:opacity .08s,transform .08s,filter .08s}} .note.active{{opacity:1;transform:scaleY(1.8);filter:brightness(1.35)}}
.playhead{{position:absolute;top:0;bottom:0;left:0;width:1px;background:#fff;opacity:.7;pointer-events:none}}
.transport{{display:flex;gap:14px;align-items:center;margin-top:22px;padding:18px 20px;background:var(--panel);border:1px solid var(--line);border-radius:8px}}
button{{border:0;border-radius:5px;background:var(--orange);color:#101820;font-weight:700;padding:11px 18px;cursor:pointer}} audio{{width:100%}}
.legend{{margin-top:22px;display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}} .legend div{{background:var(--panel);padding:16px}} .legend b{{display:block;margin-bottom:5px}}
@media(max-width:820px){{.grid{{grid-template-columns:1fr}}.score{{min-height:390px}}header{{display:block}}.legend{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<header><div><div class="tag">Synesthesia Mapper / 共感谱</div><h1>{html.escape(title)}</h1><p>同一输入、同一预设与种子将得到相同结果。点击原图可跳转到对应时间。</p></div><p>{html.escape(settings['root'])} {html.escape(settings['scale'])}<br>{settings['bpm']} BPM · {settings['scan']} scan</p></header>
<section class="grid"><div class="panel"><div class="source" id="source"><img src="{html.escape(source_name)}" alt="输入图像"><i class="scan" id="scan"></i></div><div class="info"><div><span>音符</span><b>{len(events)}</b></div><div><span>时长</span><b>{duration_seconds:.1f}s</b></div><div><span>音域</span><b>{min_note}–{max_note}</b></div><div><span>扫描</span><b>{html.escape(settings['scan'])}</b></div></div></div>
<div class="panel score" id="score">{''.join(note_markup)}<i class="playhead" id="playhead"></i></div></section>
<div class="transport"><button id="toggle">播放</button><audio id="audio" controls preload="metadata" src="synesthesia.wav"></audio></div>
<section class="legend"><div><b>纵向位置</b><p>映射音高</p></div><div><b>色相</b><p>映射音色</p></div><div><b>饱和度</b><p>映射力度</p></div><div><b>纹理密度</b><p>映射音符密度</p></div></section>
</main><script>
const audio=document.querySelector('#audio'),scan=document.querySelector('#scan'),playhead=document.querySelector('#playhead'),toggle=document.querySelector('#toggle'),source=document.querySelector('#source');
const notes=[...document.querySelectorAll('.note')]; const events={event_json};
function update(){{const duration=audio.duration||{duration_seconds:.4f};const ratio=Math.max(0,Math.min(1,audio.currentTime/duration));scan.style.left=(ratio*100)+'%';playhead.style.left=(ratio*100)+'%';notes.forEach(n=>n.classList.toggle('active',audio.currentTime>=+n.dataset.start&&audio.currentTime<=+n.dataset.end));toggle.textContent=audio.paused?'播放':'暂停';if(!audio.paused)requestAnimationFrame(update)}}
toggle.onclick=()=>audio.paused?audio.play():audio.pause();audio.onplay=update;audio.onpause=update;audio.ontimeupdate=update;
source.onclick=e=>{{const r=source.getBoundingClientRect();const ratio=(e.clientX-r.left)/r.width;audio.currentTime=ratio*(audio.duration||{duration_seconds:.4f});update()}};
</script></body></html>"""
    path.write_text(document, encoding="utf-8")


def maximum_polyphony(events: list[NoteEvent]) -> int:
    points = []
    for event in events:
        points.append((event.start_beat, 1))
        points.append((event.start_beat + event.duration_beats, -1))
    active = peak = 0
    for _, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_outputs(output_dir: Path, events: list[NoteEvent], wav_stats: dict[str, float]) -> dict[str, Any]:
    filenames = ["synesthesia.mid", "synesthesia.wav", "visual-score.png", "visual-score.svg", "interactive-preview.html", "mapping.json", "source-preview.png"]
    files = {}
    for filename in filenames:
        path = output_dir / filename
        files[filename] = {"exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0, "sha256": sha256(path) if path.is_file() else None}
    midi_ok = (output_dir / "synesthesia.mid").read_bytes()[:4] == b"MThd"
    with wave.open(str(output_dir / "synesthesia.wav"), "rb") as audio:
        wav_ok = audio.getnchannels() == 2 and audio.getsampwidth() == 2 and audio.getnframes() > 0
    with Image.open(output_dir / "visual-score.png") as score:
        score_ok = score.size == (1600, 1000)
    return {
        "status": "pass" if events and midi_ok and wav_ok and score_ok and all(item["exists"] for item in files.values()) else "fail",
        "note_count": len(events),
        "maximum_polyphony": maximum_polyphony(events),
        "midi_header_valid": midi_ok,
        "wav_valid": wav_ok,
        "score_png_valid": score_ok,
        "wav_render": wav_stats,
        "files": files,
    }


def build_settings(args: argparse.Namespace, preset: dict[str, Any]) -> dict[str, Any]:
    settings = {key: value for key, value in preset.items() if not key.startswith("_")}
    for key in ("scan", "scale", "root", "bpm", "bars", "density", "smoothness", "seed", "bands", "steps_per_bar", "min_midi", "max_midi", "sample_rate"):
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = value
    settings.setdefault("beats_per_bar", 4)
    settings["steps"] = int(settings["bars"] * settings["steps_per_bar"])
    if settings["root"].upper() not in ROOTS:
        raise ValueError(f"不支持根音 {settings['root']!r}")
    if settings["scale"] not in SCALES:
        raise ValueError(f"不支持音阶 {settings['scale']!r}")
    if settings["min_midi"] >= settings["max_midi"]:
        raise ValueError("min-midi 必须小于 max-midi")
    if not (0 <= settings["min_midi"] <= 127 and 0 <= settings["max_midi"] <= 127):
        raise ValueError("MIDI 音域必须在 0–127 之间")
    if not (30 <= settings["bpm"] <= 300):
        raise ValueError("bpm 必须在 30–300 之间")
    if not (1 <= settings["bars"] <= 64):
        raise ValueError("bars 必须在 1–64 之间")
    if not (1 <= settings["steps_per_bar"] <= 32):
        raise ValueError("steps-per-bar 必须在 1–32 之间")
    if not (2 <= settings["bands"] <= 32):
        raise ValueError("bands 必须在 2–32 之间")
    if not (8000 <= settings["sample_rate"] <= 192000):
        raise ValueError("sample-rate 必须在 8000–192000 之间")
    settings["density"] = float(clamp(settings["density"], 0.0, 1.0))
    settings["smoothness"] = float(clamp(settings["smoothness"], 0.5, 1.8))
    return settings


def run_image_to_sound(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到输入图像：{input_path}")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = load_preset(args.preset)
    settings = build_settings(args, preset)
    title = args.title or input_path.stem

    image = load_image(input_path)
    analysis_rgb = resize_for_analysis(image)
    cells = describe_cells(analysis_rgb, settings["steps"], settings["bands"], settings["scan"])
    events = map_notes(cells, settings)
    if not events:
        raise RuntimeError("没有提取到可用音符。请提高 density 或更换图像。")

    source_preview = ImageOps.fit(image, (900, 900), method=Image.Resampling.LANCZOS)
    source_preview.save(output_dir / "source-preview.png", optimize=True)
    total_beats = settings["bars"] * settings["beats_per_bar"]
    write_midi(output_dir / "synesthesia.mid", events, settings["bpm"], title)
    wav_stats = write_wav(output_dir / "synesthesia.wav", events, settings["bpm"], total_beats, settings["sample_rate"], settings["seed"])
    render_score_png(output_dir / "visual-score.png", image, events, settings, title)
    render_score_svg(output_dir / "visual-score.svg", image, events, settings, title)
    render_preview_html(output_dir / "interactive-preview.html", "source-preview.png", events, settings, title, wav_stats["duration_seconds"])

    mapping = {
        "schema": "synesthesia-mapper/image-to-sound/v1",
        "title": title,
        "source": {"path": str(input_path), "width": image.width, "height": image.height, "mode": image.mode},
        "preset": {"name": preset.get("name", args.preset), "path": preset.get("_path")},
        "settings": settings,
        "mapping_rules": {
            "time": "scan position",
            "pitch": "vertical band, quantized to scale",
            "timbre": "hue family",
            "velocity": "saturation + texture + brightness",
            "duration": "brightness + local contrast",
            "density": "texture + chroma salience",
        },
        "events": [asdict(event) for event in events],
    }
    (output_dir / "mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    validation = validate_outputs(output_dir, events, wav_stats)
    (output_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "validation": validation}, ensure_ascii=False, indent=2))
    return 0 if validation["status"] == "pass" else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="共感谱：把图像映射为 MIDI、WAV、视觉乐谱与同步预览。")
    subparsers = root.add_subparsers(dest="command", required=True)
    image = subparsers.add_parser("image-to-sound", help="把图像转换为声音与视觉乐谱")
    image.add_argument("input")
    image.add_argument("--output-dir", default="synesthesia-output")
    image.add_argument("--preset", default="prism", help="prism、grain、orbit 或预设 JSON 路径")
    image.add_argument("--title")
    image.add_argument("--scan", choices=("horizontal", "radial"))
    image.add_argument("--scale", choices=tuple(SCALES))
    image.add_argument("--root")
    image.add_argument("--bpm", type=int)
    image.add_argument("--bars", type=int)
    image.add_argument("--density", type=float)
    image.add_argument("--smoothness", type=float)
    image.add_argument("--seed", type=int)
    image.add_argument("--bands", type=int)
    image.add_argument("--steps-per-bar", type=int)
    image.add_argument("--min-midi", type=int)
    image.add_argument("--max-midi", type=int)
    image.add_argument("--sample-rate", type=int)
    image.set_defaults(func=run_image_to_sound)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
