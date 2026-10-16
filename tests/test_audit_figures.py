import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
import sys
sys.path.insert(0, str(SCRIPTS))
from audit_figures import audit_page_images, audit_workspace


class AuditFiguresTests(unittest.TestCase):
    def test_single_figure_multi_panel_clustering(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 200, 400, 600],
             'image_caption': ['图 2.4 晶胞结构 (a) 体心立方'], 'image_footnote': []},
            {'type': 'image', 'bbox': [500, 200, 800, 600],
             'image_caption': ['(b) 面心立方'], 'image_footnote': []},
        ]
        inv, reps, anoms = audit_page_images(page_num=44, blocks=blocks)
        self.assertEqual(len(inv), 2)
        # Should generate 1 update_image and 1 skip
        self.assertEqual(len(reps), 2)
        self.assertEqual(reps[0]['action'], 'update_image')
        self.assertEqual(reps[1]['action'], 'skip')
        self.assertEqual(reps[0]['new_bbox'], [97, 197, 803, 603])
        self.assertIn('图 2.4 晶胞结构 (a) 体心立方', reps[0]['image_caption'])

    def test_multiple_figure_numbers_flagged(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 100, 800, 500],
             'image_caption': ['图 1.1 结构图', '图 1.2 曲线图'], 'image_footnote': []}
        ]
        inv, reps, anoms = audit_page_images(page_num=10, blocks=blocks)
        self.assertTrue(any(a['kind'] == 'multiple_figure_numbers' for a in anoms))

    def test_inverted_reading_order_detected(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 600, 800, 900], 'image_caption': ['图 3.1 底部插图']},
            {'type': 'text', 'bbox': [100, 100, 800, 300], 'text': '物理上位于上方的正文'},
        ]
        inv, reps, anoms = audit_page_images(page_num=25, blocks=blocks)
        self.assertTrue(any(a['kind'] == 'inverted_reading_order' for a in anoms))
