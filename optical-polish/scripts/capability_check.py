#!/usr/bin/env python3
"""Report whether the runtime can execute Optical Polish."""

from __future__ import annotations

import importlib.util
import json
import platform
import sys


REQUIRED = ("PIL", "numpy")
OPTIONAL = ("cairosvg", "cv2", "fontTools")


def available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def main() -> int:
    required = {name: available(name) for name in REQUIRED}
    optional = {name: available(name) for name in OPTIONAL}
    ready = all(required.values()) and sys.version_info >= (3, 9)
    result = {
        "ready": ready,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "required": required,
        "optional": optional,
        "notes": {
            "svg": "available" if optional["cairosvg"] else "install CairoSVG or export SVG as PNG",
            "raster": "available" if ready else "install Pillow and NumPy",
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

