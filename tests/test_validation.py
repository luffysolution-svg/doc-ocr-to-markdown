import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from validate_output import validate


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'book'
        (self.root / 'images').mkdir(parents=True)
        Image.new('RGB', (12, 12), 'white').save(self.root / 'images/a.png')

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, text, name='note.md'):
        (self.root / name).write_text(text, encoding='utf-8')

    def report(self):
        return validate(self.root, self.root / 'images')

    def test_shared_asset_not_warning(self):
        self.note('# Note\n\n![a](images/a.png)\n\n![a](images/a.png)\n\n$x$\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass')
        self.assertEqual(r['total_images'], 2)
        self.assertEqual(r['duplicate_images'], 1)
        self.assertEqual(r['warnings'], [])

    def test_path_escape_rejected(self):
        Image.new('RGB', (10, 10)).save(self.root.parent / 'outside.png')
        self.note('# Note\n\n![outside](../outside.png)\n')
        self.assertEqual(self.report()['status'], 'fail')

    def test_unpaired_formula_fails(self):
        self.note('# Note\n\n![a](images/a.png)\n\n$x\n')
        self.assertEqual(self.report()['status'], 'fail')

    def test_code_dollars_not_formulas(self):
        self.note('# Note\n\n![a](images/a.png)\n\n`$HOME`\n\n```sh\n$BAD\n```\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['total_math'], 0)

    def test_nested_note_shared_image_not_orphan(self):
        (self.root / 'chapters').mkdir()
        self.note('# Root\n', '00-目录.md')
        self.note('# Chapter\n\n![a](../images/a.png)\n', 'chapters/ch.md')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['warnings'], [])


if __name__ == '__main__':
    unittest.main()
