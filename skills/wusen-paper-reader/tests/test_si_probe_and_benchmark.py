"""Tests for SI Fast-Probe and Benchmark Collector scripts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_collector import upsert_benchmark, load_benchmarks, export_markdown_table
from scripts.si_probe import KEYWORD_CATEGORIES, format_markdown_report


class BenchmarkCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "benchmarks.csv"

    def test_upsert_and_deduplicate(self):
        entry1 = {
            "doi": "10.1002/adma.202313513",
            "citation_key": "Yang-2024",
            "year": "2024",
            "journal": "Adv. Mater.",
            "catalyst_system": "Ni/NiOx@C / g-C3N4",
            "reaction_type": "Photocatalytic H2 evolution",
            "activity_rate": "10.71",
            "activity_unit": "mmol g-1 h-1",
            "aqy_percent": "40.78% @ 380 nm",
            "light_condition": "300W Xe (AM 1.5)",
            "scavenger_or_electrolyte": "10 vol% TEOA",
            "benchmark_comparison": "1.76x of 1.0 wt% Pt",
            "mechanism": "FLPs + Photothermal"
        }
        res1 = upsert_benchmark(self.csv_path, entry1)
        self.assertEqual(res1["status"], "added")
        self.assertEqual(res1["total_records"], 1)

        # Update the same entry with higher activity
        entry2 = dict(entry1)
        entry2["activity_rate"] = "10.75"
        res2 = upsert_benchmark(self.csv_path, entry2)
        self.assertEqual(res2["status"], "updated")
        self.assertEqual(res2["total_records"], 1)

        rows = load_benchmarks(self.csv_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["activity_rate"], "10.75")

    def test_export_markdown_table(self):
        entry = {
            "doi": "10.1002/adma.202313513",
            "catalyst_system": "Ni/NiOx@C / g-C3N4",
            "activity_rate": "10.71",
            "activity_unit": "mmol g-1 h-1"
        }
        upsert_benchmark(self.csv_path, entry)
        md = export_markdown_table(self.csv_path)
        self.assertIn("Ni/NiOx@C / g-C3N4", md)
        self.assertIn("10.71 mmol g-1 h-1", md)


class SIProbeTests(unittest.TestCase):
    def test_format_markdown_report(self):
        fake_result = {
            "file_name": "test_si.pdf",
            "total_pages": 42,
            "figures": [{"id": "Figure S1", "page": 5, "caption": "TGA curve"}],
            "tables": [{"id": "Table S1", "page": 30, "caption": "ESR summary"}],
            "category_matches": {
                "synthesis": [{"page": 2, "matched_keywords": ["synthesis"], "snippet": "..."}],
                "catalysis": [{"page": 4, "matched_keywords": ["her"], "snippet": "..."}],
                "electrochemical": [],
                "computation": [{"page": 6, "matched_keywords": ["dft"], "snippet": "..."}],
                "characterization": []
            }
        }
        md = format_markdown_report(fake_result)
        self.assertIn("test_si.pdf", md)
        self.assertIn("42 页", md)
        self.assertIn("Figure S1", md)
        self.assertIn("Table S1", md)
        self.assertIn("p.2", md)
        self.assertIn("p.6", md)


if __name__ == "__main__":
    unittest.main()
