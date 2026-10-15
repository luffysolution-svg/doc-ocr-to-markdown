"""Offline end-to-end fixtures: tiny PDFs, local OCR JSON and PNG assets only."""
from __future__ import annotations

import copy
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_markdown as build
from build_support import OWNERSHIP, Publisher, asset_path, load_book


def block(kind='text', text='', page=0, bbox=None, **fields):
    return {'type': kind, 'text': text, 'page_idx': page,
            'bbox': bbox or [100, 200, 600, 400], **fields}


def png(color):
    buffer = io.BytesIO()
    Image.new('RGB', (12, 10), color).save(buffer, format='PNG')
    return buffer.getvalue()


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test-build-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.raw = self.base / 'raw'
        self.raw.mkdir()
        self.out = self.base / 'out'
        self.manifest_path = self.base / 'manifest.json'
        self.chapters_path = self.base / 'chapters.json'

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def part(self, identity, items, *, nested=False, color='red'):
        folder = self.raw / identity
        ocr = folder / 'result' if nested else folder
        self.write_json(ocr / 'book_content_list.json', items)
        (ocr / 'images' / 'nested').mkdir(parents=True, exist_ok=True)
        (ocr / 'images' / 'same.png').write_bytes(png(color))
        (ocr / 'images' / 'nested' / 'qr.png').write_bytes(png(color))
        return ocr

    def book(self, *, total=2, pdf=False):
        manifest = {'pages': total, 'parts': [
            {'id': f'part{i:02}', 'start_page': i, 'end_page': i, 'pages': 1}
            for i in range(1, total + 1)]}
        sections = [{'id': f'ch{i:02}', 'title': f'传入标题{i}', 'start': i, 'end': i}
                    for i in range(1, total + 1)]
        if pdf:
            path = self.base / 'source.pdf'
            with fitz.open() as doc:
                for i in range(total):
                    page = doc.new_page(width=200, height=100)
                    color = [(1, 0, 0), (0, 0, 1)][i % 2]
                    page.draw_rect(page.rect, color=color, fill=color)
                doc.save(path)
            manifest['source'] = str(path)
        self.write_json(self.manifest_path, manifest)
        self.write_json(self.chapters_path, sections)
        return manifest, sections

    def cli(self, *args, book=False, ok=True):
        command = [sys.executable, str(ROOT / 'scripts' / 'build_markdown.py'),
                   '--raw', str(self.raw), '--out', str(self.out)]
        if book:
            command += ['--manifest', str(self.manifest_path), '--chapters', str(self.chapters_path)]
        result = subprocess.run(command + list(args), capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=30)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def text(self, name='ch01.md'):
        return (self.out / name).read_text(encoding='utf-8')

    def refs(self, text):
        return re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)

    def render(self, items, **kwargs):
        return build.build_chapter(items, None, kwargs.pop('pdf_path', None),
                                   self.base / 'render' / 'images' / 'ch', '传入标题', [],
                                   mode='book', **kwargs)

    def snapshot(self):
        return {p.relative_to(self.out).as_posix(): p.read_bytes()
                for p in self.out.rglob('*') if p.is_file()} if self.out.exists() else {}

    def test_pdf_page_offset_and_crop_coordinates(self):
        self.book(pdf=True)
        for i in (1, 2):
            self.part(f'part{i:02}', [block('image', img_path='images/same.png',
                                          bbox=[100, 200, 500, 700])])
        self.cli(book=True)
        for i, expected in [(1, (255, 0, 0)), (2, (0, 0, 255))]:
            ref = self.refs(self.text(f'ch{i:02}.md'))[0]
            with Image.open(self.out / ref) as image:
                self.assertAlmostEqual(image.width / image.height, 1.6, places=2)
                pixel = image.convert('RGB').getpixel((image.width // 2, image.height // 2))
                self.assertTrue(all(abs(a - b) < 3 for a, b in zip(pixel, expected)), pixel)

    def test_part_same_image_names_nested_tables_and_order(self):
        self.book()
        for i, color in [(1, 'red'), (2, 'blue')]:
            self.part(f'part{i:02}', [
                block(text=f'前正文{i}'),
                block('image', img_path='images/same.png', image_caption=[f'图{i} 图注']),
                block(text=f'后正文{i}'),
                block('table', table_caption=f'表{i} 表题',
                      table_body='<table><tr><td>单元格<img src="images/nested/qr.png" alt="二维码"></td></tr></table>',
                      table_footnote='整条表下注'),
                block('page_footnote', text=f'脚注{i}')
            ], nested=True, color=color)
        self.cli(book=True)
        for i, color in [(1, (255, 0, 0)), (2, (0, 0, 255))]:
            text = self.text(f'ch{i:02}.md')
            self.assertLess(text.index(f'前正文{i}'), text.index('!['))
            self.assertLess(text.index('!['), text.index(f'后正文{i}'))
            self.assertIn(f'表{i} 表题', text)
            self.assertIn(f'脚注{i}', text)
            self.assertIn('整条表下注', text)
            self.assertIn('![二维码]', text)
            for ref in self.refs(text):
                with Image.open(self.out / ref) as image:
                    self.assertEqual(image.getpixel((0, 0)), color)
        self.assertNotEqual(self.refs(self.text())[0], self.refs(self.text('ch02.md'))[0])

    def test_duplicate_references_keep_file_and_each_caption(self):
        ocr = self.part('part01', [])
        items = [block('image', img_path='images/same.png', image_caption=['图1 首次']),
                 block(text='中间正文'),
                 block('image', img_path='images/same.png', image_caption=['图2 再次'])]
        text, stats = self.render(items, ocr_dir=ocr)
        refs = self.refs(text)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0], refs[1])
        self.assertEqual(len(list((self.base / 'render').rglob('*.png'))), 1)
        self.assertIn('图1 首次', text)
        self.assertIn('图2 再次', text)
        self.assertEqual(stats['figures'], 2)

    def test_union_crop_does_not_mutate_items_or_skip_following_text(self):
        _, _ = self.book(total=1, pdf=True)
        items = [block('image', bbox=[100, 200, 500, 700], img_path='a.png'),
                 block('image', bbox=[300, 200, 800, 700], img_path='b.png'),
                 block(text='紧随图后的正文')]
        before = copy.deepcopy(items)
        text, _ = self.render(items, pdf_path=self.base / 'source.pdf')
        self.assertEqual(items, before)
        self.assertIn('紧随图后的正文', text)
        self.assertEqual(len(self.refs(text)), 1)
        with Image.open(self.base / 'render' / self.refs(text)[0]) as image:
            self.assertAlmostEqual(image.width / image.height, 2.8, places=2)

    def test_crop_failure_falls_back_to_ocr(self):
        self.book(total=1, pdf=True)
        ocr = self.part('part01', [])
        with patch.object(build.FigureStore, 'crop', return_value=None):
            text, stats = self.render([block('image', img_path='images/same.png')],
                                      ocr_dir=ocr, pdf_path=self.base / 'source.pdf')
        self.assertEqual(len(self.refs(text)), 1)
        self.assertEqual(stats['ocr_figures'], 1)

    def test_crop_exception_returns_failure(self):
        class BrokenPage:
            @property
            def rect(self):
                raise RuntimeError('synthetic rendering failure')
        self.assertIsNone(build.FigureStore(self.base).crop(BrokenPage(), (0, 0, 1, 1)))

    def test_full_page_scan_not_a_figure_or_text_suppressor(self):
        text, stats = self.render([block('image', bbox=[0, 0, 1000, 1000], img_path='scan.png'),
                                   block(text='短正文')])
        self.assertNotIn('![', text)
        self.assertIn('短正文', text)
        self.assertEqual(stats['full_page_scans_skipped'], 1)

    def test_title_one_h1_exercises_and_page_top_prose(self):
        items = [block(text='OCR错误书名', text_level=1),
                 block(text='1 概述', text_level=1),
                 block(text='1.1 正规小节', text_level=1),
                 block(text='1. 计算这个数', text_level=1),
                 block(text='(2) 计算另一个数', text_level=1),
                 block(text='这是页顶正文', bbox=[20, 0, 900, 50]),
                 block(text='下页正文', page=1)]
        text, _ = self.render(items)
        self.assertTrue(text.startswith('# 传入标题\n'))
        self.assertEqual(re.findall(r'^# .*', text, re.M), ['# 传入标题'])
        self.assertIn('\n## 1 概述\n', text)
        self.assertIn('\n1. 计算这个数\n', text)
        self.assertIn('\n(2) 计算另一个数\n', text)
        self.assertNotIn('## 这是页顶正文', text)

    def test_paragraphs_tables_equations_not_joined(self):
        text, stats = self.render([block(text='上页未完'), block(text='下页第一段', page=1),
                                   block(text='同页另一段', page=1),
                                   block('equation', text='x+1', page=1),
                                   block(text='公式后正文', page=1),
                                   block('table', page=1, table_body='<table><tr><td>数据</td></tr></table>'),
                                   block(text='表格后正文', page=1)])
        self.assertIn('上页未完\n\n下页第一段\n\n同页另一段', text)
        self.assertIn('$$\n\n公式后正文', text)
        self.assertIn('| 数据 |', text)
        self.assertEqual(stats.get('joined_page_paragraphs', 0), 0)

    def test_inline_math_and_code_preservation(self):
        text, _ = self.render([block(text=r'数值 $\cdotkg$ 与 \(x+1\)，代码 `$\cdotkg$`'),
                               block('code', code_body=r'$\cdotkg$')])
        self.assertIn(r'$\cdot kg$', text)
        self.assertIn(r'\(x+1\)', text)
        self.assertIn(r'`$\cdotkg$`', text)
        self.assertIn('```\n' + r'$\cdotkg$' + '\n```', text)

    def test_missing_part_fails_even_with_only_and_preserves_output(self):
        self.book()
        self.part('part01', [block(text='第一章')])
        self.out.mkdir()
        (self.out / 'manual.txt').write_text('人工内容', encoding='utf-8')
        before = self.snapshot()
        result = self.cli('--only', 'ch01', book=True, ok=False)
        self.assertIn('part02', result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_ranges_reject_overlap_omission_and_out_of_bounds(self):
        original, sections = self.book()
        for i in (1, 2):
            self.part(f'part{i:02}', [block(text='正文')])
        variants = []
        m = copy.deepcopy(original); m['parts'][1]['start_page'] = 1; variants.append((m, sections))
        m = copy.deepcopy(original); m['pages'] = 3; variants.append((m, sections))
        s = copy.deepcopy(sections); s[1]['end'] = 3; variants.append((original, s))
        s = copy.deepcopy(sections); s[1]['start'] = 1; variants.append((original, s))
        variants.append((original, sections[:1]))
        for manifest, chapter in variants:
            with self.subTest(manifest=manifest, sections=chapter):
                self.write_json(self.manifest_path, manifest)
                self.write_json(self.chapters_path, chapter)
                self.cli(book=True, ok=False)
                self.assertFalse(self.out.exists())

    def test_ocr_and_pdf_page_bounds_fail(self):
        self.book(total=1, pdf=True)
        self.part('part01', [block(text='越界', page=1)])
        self.cli(book=True, ok=False)
        self.assertFalse(self.out.exists())
        self.part('part01', [block(text='越界', page=-1)])
        self.cli(book=True, ok=False)
        self.part('part01', [block(text='越界', page=True)])
        self.cli(book=True, ok=False)

    def test_path_escape_inputs_and_outputs_fail(self):
        manifest, sections = self.book(total=1)
        self.part('part01', [block(text='正文')])
        for unsafe in ('../escape.md', '/escape.md', 'C:/escape.md', 'x\\escape.md',
                       '00-目录.md', 'build-report.json', 'images/x.md'):
            with self.subTest(unsafe=unsafe):
                chapter = copy.deepcopy(sections); chapter[0]['file'] = unsafe
                self.write_json(self.chapters_path, chapter)
                self.cli(book=True, ok=False)
                self.assertFalse(self.out.exists())
        self.write_json(self.chapters_path, sections)
        manifest['parts'][0]['id'] = '../escape'
        self.write_json(self.manifest_path, manifest)
        self.cli(book=True, ok=False)

    def test_figure_and_table_asset_escape_rejected_even_with_pdf(self):
        self.book(total=1, pdf=True)
        ocr = self.part('part01', [])
        for unsafe in ('../outside.png', '/outside.png', 'C:\\outside.png', '//host/image.png'):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    self.render([block('image', img_path=unsafe)], ocr_dir=ocr,
                                pdf_path=self.base / 'source.pdf')
                with self.assertRaises(ValueError):
                    self.render([block('table', table_body=f'<table><tr><td><img src="{unsafe}"></td></tr></table>')], ocr_dir=ocr)

    def test_only_preserves_other_chapters_index_report_review_and_assets(self):
        self.book()
        for i in (1, 2):
            self.part(f'part{i:02}', [block(text=f'正文{i}')])
        self.cli(book=True)
        other = (self.out / 'ch02.md').read_bytes()
        (self.out / 'ch02.md').write_bytes(other + b'\nmanual edit\n')
        (self.out / 'manual.md').write_text('人工文稿', encoding='utf-8')
        image = self.out / 'images' / 'ch02' / 'manual.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(png('green'))
        self.part('part01', [block(text='更新第一章')])
        self.cli('--only', 'ch01', book=True)
        report = json.loads(self.text('build-report.json'))
        self.assertEqual([c['id'] for c in report['chapters']], ['ch01', 'ch02'])
        self.assertIn('ch02', report['review'])
        self.assertIn('ch02.md', self.text('00-目录.md'))
        self.assertEqual((self.out / 'ch02.md').read_bytes(), other + b'\nmanual edit\n')
        self.assertIn('更新第一章', self.text())
        self.assertTrue(image.exists())
        self.assertEqual(self.text('manual.md'), '人工文稿')

    def test_unowned_collision_never_overwritten_and_preflight_is_whole_batch(self):
        self.book()
        for i in (1, 2):
            self.part(f'part{i:02}', [block(text='正文')])
        self.out.mkdir()
        (self.out / 'ch02.md').write_text('人工文稿', encoding='utf-8')
        before = self.snapshot()
        for args in [(), ('--overwrite',)]:
            self.cli(*args, book=True, ok=False)
            self.assertEqual(self.snapshot(), before)

    def test_owned_manual_edits_require_overwrite(self):
        self.book(total=1)
        self.part('part01', [block(text='原正文')])
        self.cli(book=True)
        (self.out / 'ch01.md').write_text('人工修改', encoding='utf-8')
        before = self.snapshot()
        self.part('part01', [block(text='更新正文')])
        self.cli(book=True, ok=False)
        self.assertEqual(self.snapshot(), before)
        self.cli('--overwrite', book=True)
        self.assertIn('更新正文', self.text())
        self.assertIn('ch01.md', json.loads(self.text(OWNERSHIP))['files'])

    def test_clean_refused_and_unowned_index_report_preserved(self):
        self.part('ch01', [block(text='正文')])
        self.out.mkdir()
        for name in ['00-目录.md', 'build-report.json']:
            path = self.out / name
            path.write_text('人工内容', encoding='utf-8')
            before = self.snapshot()
            self.cli('--clean', ok=False)
            self.cli('--overwrite', ok=False)
            self.assertEqual(self.snapshot(), before)
            path.unlink()

    def test_regular_only_keeps_index_and_unknown_only_fails(self):
        for i in (1, 2):
            self.part(f'ch{i:02}', [block(text=f'正文{i}')])
        self.cli('--mode', 'book')
        self.part('ch01', [block(text='更新正文')])
        self.cli('--mode', 'book', '--only', 'ch01')
        self.assertIn('ch02.md', self.text('00-目录.md'))
        before = self.snapshot()
        self.cli('--mode', 'book', '--only', 'missing', ok=False)
        self.assertEqual(self.snapshot(), before)

    def test_nested_chapter_links_and_single_index(self):
        _, sections = self.book(total=1)
        sections[0]['file'] = 'chapters/ch01.md'
        self.write_json(self.chapters_path, sections)
        self.part('part01', [block('image', img_path='images/same.png')])
        self.cli(book=True)
        text = self.text('chapters/ch01.md')
        ref = self.refs(text)[0]
        self.assertTrue(ref.startswith('../images/'))
        self.assertTrue((self.out / 'chapters' / ref).is_file())
        self.assertIn('chapters/ch01.md', self.text('00-目录.md'))

    def test_output_parent_file_conflict_fails_without_changes(self):
        self.book(total=1)
        self.part('part01', [block('image', img_path='images/same.png')])
        self.out.mkdir()
        (self.out / 'images').write_text('人工文件', encoding='utf-8')
        before = self.snapshot()
        self.cli(book=True, ok=False)
        self.assertEqual(self.snapshot(), before)

    def test_pdf_total_mismatch_and_regular_ocr_bounds_fail(self):
        manifest, sections = self.book(total=2, pdf=True)
        manifest['pages'] = 1
        manifest['parts'] = manifest['parts'][:1]
        self.write_json(self.manifest_path, manifest)
        self.write_json(self.chapters_path, sections[:1])
        self.part('part01', [block(text='正文')])
        self.cli(book=True, ok=False)
        self.assertFalse(self.out.exists())
        self.part('part01', [block(text='越界', page=2)])
        self.cli('--pdf', str(self.base / 'source.pdf'), ok=False)
        self.assertFalse(self.out.exists())

    def test_missing_nested_asset_and_fragment_fallback_fail(self):
        ocr = self.part('part01', [])
        with self.assertRaisesRegex(ValueError, '内嵌图片'):
            self.render([block('table', table_body='<table><tr><td><img src="missing.png"></td></tr></table>')], ocr_dir=ocr)
        with self.assertRaisesRegex(ValueError, '回退图'):
            self.render([block('image', img_path='images/same.png', bbox=[100, 200, 500, 700]),
                         block('image', img_path='missing.png', bbox=[300, 200, 800, 700])], ocr_dir=ocr)

    def test_process_repairs_inline_boundary_without_moving_prose(self):
        text, _ = self.render([block(text='正文（$x)$。')])
        self.assertIn('正文（$x$）。', text)

    def test_output_and_asset_symlinks_rejected_when_supported(self):
        outside = self.base / 'outside'
        outside.mkdir()
        try:
            self.out.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Windows symlinks require permission')
        with self.assertRaises(ValueError):
            Publisher(self.out)
        (outside / 'image.png').write_bytes(png('red'))
        (self.raw / 'link.png').symlink_to(outside / 'image.png')
        with self.assertRaises(ValueError):
            asset_path(self.raw, 'link.png')


if __name__ == '__main__':
    unittest.main()
