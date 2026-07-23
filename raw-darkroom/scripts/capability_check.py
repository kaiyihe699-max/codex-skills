#!/usr/bin/env python3
"""Report whether RAW Darkroom can run in the current environment."""

from __future__ import annotations

import importlib
import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
ICC_PROFILE_PATH = SKILL_DIR / "assets" / "srgb-display-profile.b64"


def present(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> int:
    required = {name: present(name) for name in ("rawpy", "numpy", "PIL", "tifffile")}
    rawpy_info = {}
    if required["rawpy"]:
        rawpy = importlib.import_module("rawpy")
        rawpy_info = {
            "rawpy_version": rawpy.__version__,
            "libraw_version": ".".join(str(part) for part in rawpy.libraw_version),
            "features": dict(rawpy.flags),
        }
    icc_profile_asset = ICC_PROFILE_PATH.is_file() and ICC_PROFILE_PATH.stat().st_size > 0
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "required_modules": required,
        "exiftool_optional": shutil.which("exiftool") is not None,
        "icc_profile_asset": icc_profile_asset,
        "ready": all(required.values()) and icc_profile_asset,
        **rawpy_info,
        "notes": [
            "核心解码、传感器分析和显影需要 rawpy、NumPy、Pillow、tifffile。",
            "ExifTool 仅用于补充镜头与厂商元数据；缺失时仍可完成核心流程。",
            "所有显示参考输出都需要内置 sRGB ICC 资产。",
            "具体相机与 RAW 格式支持由当前 LibRaw 版本决定。",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
