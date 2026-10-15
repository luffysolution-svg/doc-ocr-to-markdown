import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from mineru_ocr import split_pdf, submit, valid_id


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / '教材.pdf'
        with fitz.open() as pdf:
            for _ in range(5):
                pdf.new_page()
            pdf.save(self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, size=2, prefix='part'):
        return argparse.Namespace(file=str(self.source), out_root=str(self.root / 'work'),
                                  chunk_size=size, prefix=prefix)

    def test_page_mapping_and_source_preserved(self):
        before = self.source.read_bytes()
        split_pdf(self.args())
        manifest = json.loads((self.root / 'work/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual([(p['start_page'], p['end_page']) for p in manifest['parts']],
                         [(1, 2), (3, 4), (5, 5)])
        for p in manifest['parts']:
            with fitz.open(p['path']) as doc:
                self.assertEqual(len(doc), p['pages'])
        self.assertEqual(before, self.source.read_bytes())

    def test_invalid_parameters_write_nothing(self):
        for size, prefix in [(0, 'part'), (-1, 'part'), (2, '../escape'), (2, 'C:\\oops')]:
            with self.subTest(size=size, prefix=prefix), self.assertRaises(ValueError):
                split_pdf(self.args(size, prefix))
            self.assertFalse((self.root / 'work').exists())

    def test_rerun_does_not_overwrite(self):
        split_pdf(self.args())
        sentinel = self.root / 'work/chunks/part01.pdf'
        original = sentinel.read_bytes()
        with self.assertRaises(ValueError):
            split_pdf(self.args())
        self.assertEqual(original, sentinel.read_bytes())

    def test_submit_duplicate_ids_never_calls_api(self):
        ids = self.root / 'ids.tsv'
        ids.write_text(f'{self.source}\tsame\n{self.source}\tsame\n', encoding='utf-8')
        args = argparse.Namespace(out_root=str(self.root / 'ocr'), id_file=str(ids), files=[])
        with patch('mineru_ocr.api') as api, self.assertRaises(ValueError):
            submit(args)
        api.assert_not_called()

    def test_existing_job_never_resubmits(self):
        work = self.root / 'ocr'
        work.mkdir()
        (work / 'job.json').write_text('{}')
        with patch('mineru_ocr.api') as api, self.assertRaises(ValueError):
            submit(argparse.Namespace(out_root=str(work)))
        api.assert_not_called()

    def test_identifier_validation(self):
        self.assertEqual(valid_id('part01'), 'part01')
        for value in ['', '../x', '/x', 'x\\y', 'x:y', 'a' * 65]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                valid_id(value)


if __name__ == '__main__':
    unittest.main()
