#!/usr/bin/env python3
"""Report whether the local runtime can execute Synesthesia Mapper."""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sys


def module_status(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> int:
    modules = {name: module_status(name) for name in ("PIL", "numpy")}
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "required_modules": modules,
        "ffmpeg_optional": shutil.which("ffmpeg") is not None,
        "image_to_sound_ready": all(modules.values()),
        "audio_to_visual_ready": False,
        "notes": [
            "图像转 MIDI/WAV/视觉乐谱需要 Pillow 和 NumPy。",
            "第一版暂未实现音频转视觉；ffmpeg 仅为后续动态海报功能预留。",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["image_to_sound_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
