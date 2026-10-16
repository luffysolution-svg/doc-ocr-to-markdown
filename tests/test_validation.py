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

    def test_unclosed_fence_reports_hidden_image(self):
        self.note('# Note\n\n```\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'fail', r)
        self.assertTrue(any('围栏未闭合' in error for error in r['errors']))
        self.assertEqual(r['detail'][0]['source_image_candidates'], 1)
        self.assertEqual(r['detail'][0]['parsed_images'], 0)

    def test_duplicate_fences_warn_about_swallowed_image(self):
        self.note('# Note\n\n```\n```python\nprint(1)\n```\n```\n\n![a](images/a.png)\n\n```\n')
        r = self.report()
        self.assertTrue(any('图片引用' in warning and '遮蔽' in warning for warning in r['warnings']))
        self.assertEqual(r['total_images'], 0)

    def test_labelled_code_image_example_is_allowed(self):
        self.note('# Note\n\n```markdown\n![example](missing.png)\n```\n\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['warnings'], [])
        self.assertEqual(r['detail'][0]['source_image_candidates'], 2)
        self.assertEqual(r['detail'][0]['parsed_images'], 1)

    def test_closed_fences_in_containers_are_allowed(self):
        self.note('# Note\n\n> ```markdown\n> ![example](missing.png)\n> ```\n\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['warnings'], [])

    def test_closed_fences_allow_non_markdown_line_separators(self):
        for separator in ('\u2028', '\v', '\f'):
            with self.subTest(separator=repr(separator)):
                self.note('# Note\n\n```text\nleft' + separator + 'right\n```\n\n![a](images/a.png)\n')
                r = self.report()
                self.assertEqual(r['status'], 'pass', r)
                self.assertEqual(r['warnings'], [])

    def test_source_candidates_count_adjacent_images_separately(self):
        self.note('# Note\n\n![a](images/a.png) ![b](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['detail'][0]['source_image_candidates'], 2)
        self.assertEqual(r['detail'][0]['parsed_images'], 2)

    def test_missing_image_still_counts_as_parsed(self):
        self.note('# Note\n\n![missing](images/missing.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'fail')
        self.assertEqual(r['detail'][0]['parsed_images'], 1)
        self.assertEqual(r['total_images'], 0)

    def test_orphan_details_do_not_delete_files(self):
        orphan = self.root / 'images/unused.png'
        Image.new('RGB', (10, 10)).save(orphan)
        self.note('# Note\n\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['orphan_images'], ['images/unused.png'])
        self.assertTrue(any('images/unused.png' in warning for warning in r['warnings']))
        self.assertTrue(orphan.exists())


if __name__ == '__main__':
    unittest.main()
