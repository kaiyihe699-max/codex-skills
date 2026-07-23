from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image


SKILL_DIR = Path(__file__).resolve().parents[1]
RAW_DARKROOM = SKILL_DIR / "scripts" / "raw_darkroom.py"
CREATE_TEST_DNG = SKILL_DIR / "scripts" / "create_test_dng.py"


def load_raw_darkroom():
    spec = importlib.util.spec_from_file_location("raw_darkroom_under_test", RAW_DARKROOM)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 raw_darkroom.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RawDarkroomUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_raw_darkroom()

    def test_libraw_flip_6_is_clockwise(self) -> None:
        source = np.arange(6).reshape(2, 3)
        expected = np.rot90(source, 3)
        np.testing.assert_array_equal(self.module.orient_array(source, 6), expected)

    def test_sampling_stride_does_not_alias_cfa_period(self) -> None:
        self.assertEqual(self.module.cfa_safe_stride(2, np.zeros((2, 2))), 3)
        self.assertEqual(self.module.cfa_safe_stride(3, np.zeros((6, 6))), 5)

    def test_private_metadata_redaction_is_recursive(self) -> None:
        payload = {
            "SourceFile": "C:/private/input.dng",
            "Nested": {"GPSLatitude": 31.2, "LensModel": "Test Lens"},
        }
        redacted = self.module.redact_private_metadata(payload)
        self.assertEqual(redacted["SourceFile"], "[redacted]")
        self.assertEqual(redacted["Nested"]["GPSLatitude"], "[redacted]")
        self.assertEqual(redacted["Nested"]["LensModel"], "Test Lens")

    def test_overwrite_refuses_unknown_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raw-darkroom-output-") as temporary:
            output = Path(temporary)
            (output / "user-file.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.module.prepare_output_dir(
                    output,
                    [{"id": "01-neutral"}],
                    overwrite=True,
                )
            self.assertTrue((output / "user-file.txt").is_file())


class RawDarkroomIntegrationTests(unittest.TestCase):
    def test_portrait_pipeline_profiles_privacy_and_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raw-darkroom-test-") as temporary:
            root = Path(temporary)
            source = root / "portrait.dng"
            output = root / "output"
            subprocess.run(
                [
                    sys.executable,
                    str(CREATE_TEST_DNG),
                    str(source),
                    "--width",
                    "192",
                    "--height",
                    "128",
                    "--orientation",
                    "6",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(RAW_DARKROOM),
                str(source),
                "--output-dir",
                str(output),
                "--half-size",
            ]
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
            metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
            analysis = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["status"], "pass")
            self.assertTrue(validation["candidate_count_valid"])
            self.assertTrue(validation["report_check"]["image_references_valid"])
            self.assertEqual(metadata["source"]["path"], source.name)
            self.assertFalse(metadata["source"]["absolute_path_included"])
            self.assertEqual(metadata["processing"]["libraw_orientation_applied"], 6)
            self.assertIn("limiting_highlight_channel", analysis["measurement"])

            with Image.open(output / "sensor-preview.jpg") as preview:
                preview_size = preview.size
                self.assertGreater(preview.height, preview.width)
                self.assertTrue(preview.info.get("icc_profile"))
            with Image.open(output / "01-neutral.jpg") as jpeg:
                self.assertEqual(jpeg.size, preview_size)
                self.assertTrue(jpeg.info.get("icc_profile"))
            with tifffile.TiffFile(output / "01-neutral.tiff") as tiff:
                self.assertEqual(tiff.pages[0].dtype, np.dtype("uint16"))
                self.assertIn(34675, tiff.pages[0].tags)

            refused = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("输出目录不是空目录", refused.stderr)
            subprocess.run(
                [*command, "--overwrite"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )


if __name__ == "__main__":
    unittest.main()
