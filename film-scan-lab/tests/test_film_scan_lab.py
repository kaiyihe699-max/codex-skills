from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_DIR / "scripts" / "film_scan_lab.py"
GENERATOR_PATH = SKILL_DIR / "scripts" / "create_test_scan.py"
SPEC = importlib.util.spec_from_file_location("film_scan_lab", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FilmScanLabTests(unittest.TestCase):
    def test_density_inversion_orders_scene_brightness(self) -> None:
        positive = np.zeros((32, 64, 3), dtype=np.float32)
        positive[:, :32] = 0.2
        positive[:, 32:] = 0.8
        base = np.array([0.85, 0.58, 0.31], dtype=np.float32)
        base_density = -np.log10(base)
        density = base_density + 0.1 + positive * np.array([1.1, 1.2, 1.3], dtype=np.float32)
        scan = np.power(10.0, -density)
        profile = MODULE.select_profile(
            MODULE.load_catalog(SKILL_DIR / "assets" / "film-profiles.json"),
            "generic-c41",
        )
        result, _ = MODULE.invert_film(scan, "color-negative", base, profile)
        self.assertGreater(float(np.mean(result[:, 32:])), float(np.mean(result[:, :32])))

    def test_hdr_merge_returns_finite_image(self) -> None:
        image = np.linspace(0.01, 0.95, 64 * 48 * 3, dtype=np.float32).reshape(48, 64, 3)
        merged, report = MODULE.merge_hdr_scans([image, np.clip(image * 0.5, 0, 1), np.clip(image * 1.5, 0, 1)])
        self.assertEqual(merged.shape, image.shape)
        self.assertTrue(np.all(np.isfinite(merged)))
        self.assertTrue(report["enabled"])

    def test_end_to_end_with_hdr_ir_and_edge_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            subprocess.run(
                [sys.executable, str(GENERATOR_PATH), "--output-dir", str(source), "--width", "480", "--height", "320"],
                check=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    str(source / "scan-mid.tiff"),
                    "--hdr", str(source / "scan-dark.tiff"), str(source / "scan-bright.tiff"),
                    "--ir", str(source / "infrared.png"),
                    "--edge-code", "KODAK 400-3 PORTRA 400",
                    "--output-dir", str(output),
                    "--film-type", "color-negative",
                    "--capture-profile", "generic-scanner-srgb",
                    "--grain", "rebuild",
                ],
                check=True,
            )
            validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
            analysis = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["status"], "pass")
            self.assertEqual(analysis["film"]["profile_id"], "kodak-portra-400")
            self.assertTrue(analysis["hdr"]["enabled"])
            self.assertIn(analysis["infrared"]["status"], {"accepted", "rejected-conflict"})
            self.assertGreaterEqual(analysis["defects"]["scratch_candidate_count"], 1)
            self.assertEqual(analysis["edge_code"]["status"], "manual")


if __name__ == "__main__":
    unittest.main()
