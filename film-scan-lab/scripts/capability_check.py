from __future__ import annotations

import importlib.util
import json
import shutil
import sys


REQUIRED = ["numpy", "cv2", "PIL", "tifffile"]
OPTIONAL = ["rawpy", "pytesseract"]


def main() -> int:
    required = {name: bool(importlib.util.find_spec(name)) for name in REQUIRED}
    optional = {name: bool(importlib.util.find_spec(name)) for name in OPTIONAL}
    optional["tesseract_binary"] = bool(shutil.which("tesseract"))
    result = {
        "status": "pass" if all(required.values()) else "fail",
        "required": required,
        "optional": optional,
        "notes": {
            "rawpy": "Required only for camera RAW input.",
            "tesseract_binary": "Without it, edge strips are exported for manual code entry.",
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
