"""Tests for SI Figure Extractor and Subfigure Cropper."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image
from scripts.crop_subfigures import crop_image_box, crop_subfigures


class CropSubfiguresTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)

        # Create a synthetic 200x100 test image
        self.test_img = Image.new("RGB", (200, 100), color="white")
        self.src_file = self.temp_path / "master_test.png"
        self.test_img.save(self.src_file)

    def test_crop_image_box_normalized(self):
        # crop top-left quadrant [ymin, xmin, ymax, xmax] = [0.0, 0.0, 0.5, 0.5]
        cropped = crop_image_box(self.test_img, (0.0, 0.0, 0.5, 0.5), normalized=True)
        self.assertEqual(cropped.size, (100, 50))

    def test_crop_subfigures_batch(self):
        specs = [
            {"name": "panel_a", "box": [0.0, 0.0, 0.5, 0.5], "description": "top left"},
            {"name": "panel_b", "box": [0.0, 0.5, 0.5, 1.0], "description": "top right"}
        ]
        out_dir = self.temp_path / "subfigs"
        results = crop_subfigures(self.src_file, out_dir, specs)

        self.assertEqual(len(results), 2)
        self.assertTrue((out_dir / "panel_a.png").exists())
        self.assertTrue((out_dir / "panel_b.png").exists())
        manifest = out_dir / "master_test_crops_manifest.json"
        self.assertTrue(manifest.exists())
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)


if __name__ == "__main__":
    unittest.main()
