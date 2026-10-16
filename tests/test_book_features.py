"""Offline regression tests for explicit repairs and source-exact book indexes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from book_features import apply_repairs, make_chunks, validate_agent_index, write_agent_index
from build_support import load_book


BOX = [100, 200, 600, 400]


def block(text='原始正文', page=1, kind='text', bbox=None):
    return {'type': kind, 'text': text, 'bbox': list(bbox or BOX),
            '_page': page, 'page_idx': 0, '_ocr_dir': 'ocr/分片/result'}


def repair(**fields):
    return {'page': 1, 'bbox': list(BOX), 'field': 'text', 'old': '原始正文',
            'new': '校正正文', 'reason': '已核对源页', **fields}


class RepairTests(unittest.TestCase):
    def test_exact_replacement_and_explicit_equation_to_prose_do_not_mutate(self):
        original = [block(kind='equation')]
        records = [repair(set_type='body_text', new='纯正文，含 α 与 Unicode 字符。')]
        before, before_records = copy.deepcopy(original), copy.deepcopy(records)
        fixed, audit = apply_repairs(original, records)
        self.assertEqual(original, before)
        self.assertEqual(records, before_records)
        self.assertEqual(fixed[0]['type'], 'body_text')
        self.assertEqual(fixed[0]['text'], records[0]['new'])
        self.assertNotIn('$$', fixed[0]['text'])
        self.assertEqual(fixed[0]['page_idx'], 0)
        self.assertEqual(audit, [{**records[0], 'applied': True}])
        fixed[0]['bbox'][0] = 1
        audit[0]['bbox'][0] = 2
        self.assertEqual(original, before)
        self.assertEqual(records, before_records)

    def test_no_implicit_formula_guess_or_type_change(self):
        fixed, _ = apply_repairs([block(kind='equation')], [repair(new='明确的正文')])
        self.assertEqual(fixed[0]['type'], 'equation')
        self.assertEqual(fixed[0]['text'], '明确的正文')
        original = [block()]
        fixed, audit = apply_repairs(original, [])
        self.assertEqual(fixed, original)
        self.assertIsNot(fixed[0], original[0])
        self.assertEqual(audit, [])

    def test_skip_and_type_repair_preserve_full_audit(self):
        records = [dict(page=1, bbox=list(BOX), action='skip', old='原始正文', reason='确认页眉'),
                   dict(page=2, bbox=list(BOX), action='set_type', old='脚注',
                        set_type='page_footnote', reason='确认脚注')]
        original = [block(), block('脚注', page=2)]
        fixed, audit = apply_repairs(original, records)
        self.assertEqual([b['type'] for b in fixed], ['page_footnote'])
        self.assertEqual(len(original), 2)
        self.assertEqual(audit, [{**record, 'applied': True} for record in records])

    def test_stale_duplicate_conflicting_ambiguous_or_missing_repairs_fail(self):
        for original, records in [
            ([block()], [repair(old='旧版本')]),
            ([block()], [repair(), repair()]),
            ([block()], [repair(), repair(old='校正正文', new='再次修改')]),
            ([block(), block()], [repair()]),
            ([block()], [repair(bbox=[10, 20, 30, 40])]),
            ([block()], [repair(set_type='image')]),
            ([block()], [repair(field='type')]),
            ([block()], [repair(reason='')]),
            ([block()], [repair(action='invent')]),
            ([block()], [repair(new=None)]),
        ]:
            before = copy.deepcopy(original)
            with self.subTest(records=records):
                with self.assertRaises(ValueError):
                    apply_repairs(original, records)
                self.assertEqual(original, before)

    def test_page_and_coordinate_bounds_fail(self):
        for changes in ({'page': 0}, {'page': True}, {'page': 2},
                        {'bbox': [-1, 0, 100, 100]}, {'bbox': [0, 0, 1001, 1000]},
                        {'bbox': [100, 200, 100, 400]}, {'bbox': [0, 0, float('nan'), 1]},
                        {'bbox': [False, 0, 10, 10]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                apply_repairs([block()], [repair(**changes)])

    def test_multishard_global_page_targets_do_not_change_local_indices(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('part-a', 'part-b'):
                target = root / name / 'result'
                target.mkdir(parents=True)
                items = [{'type': 'text', 'text': '相同文本', 'bbox': BOX, 'page_idx': 0}]
                (target / 'book_content_list.json').write_text(json.dumps(items), encoding='utf-8')
            manifest = {'pages': 2, 'parts': [
                {'id': 'part-a', 'start_page': 1, 'end_page': 1},
                {'id': 'part-b', 'start_page': 2, 'end_page': 2}]}
            sections = [{'id': 'all', 'title': '测试书名', 'start': 1, 'end': 2}]
            original = load_book(root, manifest, sections, None)
            fixed, audit = apply_repairs(original, [repair(page=2, old='相同文本', new='第二分片修复')])
            self.assertEqual([b['text'] for b in fixed], ['相同文本', '第二分片修复'])
            self.assertEqual([b['page_idx'] for b in fixed], [0, 0])
            self.assertEqual([b['_page'] for b in fixed], [1, 2])
            self.assertEqual(audit[0]['page'], 2)
            self.assertEqual(original[1]['text'], '相同文本')

    def test_image_insertion_uses_original_anchor_and_retains_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf_path = Path(folder) / 'source.pdf'
            with fitz.open() as pdf:
                pdf.new_page()
                pdf.save(pdf_path)
            image = {'page': 1, 'bbox': [200, 500, 800, 900], 'action': 'add_image',
                     'reason': '源PDF存在漏图', 'after': {'bbox': BOX, 'text': '原始正文'}}
            original = [block(), block('随后正文', bbox=[10, 910, 900, 980])]
            fixed, audit = apply_repairs(original, [repair(), image], pdf_path)
            self.assertEqual([b['type'] for b in fixed], ['text', 'image', 'text'])
            self.assertEqual(fixed[0]['text'], '校正正文')
            self.assertEqual(fixed[2]['text'], '随后正文')
            self.assertEqual(fixed[1]['bbox'], image['bbox'])
            for key in ('page_idx', '_page', '_ocr_dir'):
                self.assertEqual(fixed[1][key], original[0][key])
            self.assertTrue(fixed[1]['_added_image'])
            self.assertEqual(audit[1], {**image, 'applied': True})
            for blocks, records, pdf in [
                (original, [image], None),
                (original, [image, image], pdf_path),
                (original, [image, dict(page=1, bbox=BOX, action='skip',
                                        old='原始正文', reason='冲突跳过')], pdf_path),
                ([block(), block()], [image], pdf_path),
                (original, [{**image, 'after': {'bbox': BOX, 'text': '过期锚点'}}], pdf_path),
                ([block(page=2)], [repair(page=2)], pdf_path),
            ]:
                with self.subTest(records=records), self.assertRaises(ValueError):
                    apply_repairs(blocks, records, pdf)


class ChunkTests(unittest.TestCase):
    def test_unicode_line_numbers_reconstruction_and_page_mapping(self):
        text = '\n# 源书：α\n\n## 第一节\n\n第一段：😀 café。\n\n第二段：Ω。\n\n## 第二节\n\n末段，无末尾换行'
        pages = [{'pdf_page': 97, 'start_line': 4, 'end_line': 8, 'status': 'rendered'},
                 {'pdf_page': 98, 'start_line': 8, 'end_line': 12, 'status': 'rendered'},
                 {'pdf_page': 99, 'start_line': None, 'end_line': None, 'status': 'empty'}]
        chunks = make_chunks(text, '章节/01.md', '源书：α', pages, max_chars=22)
        self.assertEqual(''.join(c['text'] for c in chunks), text)
        lines = text.splitlines(keepends=True)
        cursor = 1
        for chunk in chunks:
            self.assertEqual(chunk['start_line'], cursor)
            self.assertEqual(chunk['text'], ''.join(lines[cursor - 1:chunk['end_line']]))
            self.assertEqual(chunk['file'], '章节/01.md')
            self.assertNotIn(99, chunk['pages'])
            cursor = chunk['end_line'] + 1
        self.assertEqual(cursor, len(lines) + 1)
        cross_page = next(c for c in chunks if '第二段' in c['text'])
        self.assertEqual(cross_page['pages'], [97, 98])
        for chunk in chunks:
            if '末段' in chunk['text']:
                self.assertEqual(chunk['section'], '源书：α > 第二节')

    def test_display_math_blank_lines_table_and_fence_remain_atomic(self):
        math = '$$\na=1\n\n# 这不是标题\n\nb=2\n$$\n'
        table = '| 项 | 值 |\n| --- | --- |\n| α | $x$ |\n| β | 二 |\n'
        code = '```python\n# 也不是标题\n\nprint("$$")\n```\n'
        text = '# 书\n\n## 正式节\n\n正文\n\n' + math + '\n' + table + '\n' + code + '\n结尾\n'
        chunks = make_chunks(text, 'book.md', '书', [], max_chars=12)
        self.assertEqual(''.join(c['text'] for c in chunks), text)
        for atomic in (math, table, code):
            matches = [c for c in chunks if atomic in c['text']]
            self.assertEqual(len(matches), 1, atomic)
            self.assertGreater(len(matches[0]['text']), 12)
            self.assertEqual(matches[0]['section'], '书 > 正式节')

    def test_nested_headings_setext_and_large_limit_sections(self):
        text = '# 书\n\n## 甲\n\n正文\n\n### 子节\n\n细节\n\n乙\n---\n\n第二节\n'
        chunks = make_chunks(text, 'a.md', '书', [], max_chars=9999)
        self.assertEqual([c['section'] for c in chunks], ['书', '书 > 甲', '书 > 甲 > 子节', '书 > 乙'])
        self.assertEqual(''.join(c['text'] for c in chunks), text)

    def test_list_and_html_table_are_indivisible_blocks(self):
        listing = '- 第一项\n\n  第二段\n\n- 第二项\n'
        table = '<table>\n<tr><td>中文</td></tr>\n<tr><td>α</td></tr>\n</table>\n'
        text = '# 书\n\n' + listing + '\n' + table + '\n结尾\n'
        chunks = make_chunks(text, 'a.md', '书', [], max_chars=5)
        self.assertEqual(''.join(c['text'] for c in chunks), text)
        for atomic in (listing, table):
            self.assertEqual(sum(atomic in c['text'] for c in chunks), 1)

    def test_invalid_page_ranges_and_limits_fail(self):
        for pages in ([{'pdf_page': 0, 'start_line': 1, 'end_line': 1, 'status': 'rendered'}],
                      [{'pdf_page': 1, 'start_line': 1, 'end_line': 20, 'status': 'rendered'}],
                      [{'pdf_page': 1, 'start_line': None, 'end_line': 1, 'status': 'empty'}],
                      [{'pdf_page': 1, 'start_line': 1, 'end_line': 1, 'status': 'rendered'}] * 2):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                make_chunks('正文\n', 'a.md', '书', pages)
        for limit in (0, -1, True, 3.2):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                make_chunks('正文', 'a.md', '书', [], max_chars=limit)

    def test_crlf_exact_preservation_and_whitespace_only(self):
        for text in ('# 书\r\n\r\n正文\r\n\r\n', '\n\n', '单行', ''):
            chunks = make_chunks(text, 'a.md', '书', [], max_chars=4)
            self.assertEqual(''.join(c['text'] for c in chunks), text)

    def test_heading_with_inline_math_is_a_real_section(self):
        text = '# 书\n\n## 能量 $E$\n\n能量正文\n'
        chunks = make_chunks(text, 'a.md', '书', [], max_chars=9999)
        self.assertEqual(chunks[-1]['section'], '书 > 能量 $E$')
        self.assertIn('能量正文', chunks[-1]['text'])


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.text = '# 书名 Ω\n\n## 第一节\n\n中文正文与 $x$。\n\n$$\nx=2\n\n+y\n$$\n\n结束。\n'
        self.chapters = [{'id': 'ch1', 'file': '章节/ch1.md', 'title': '书名 Ω'}]
        self.pages = {'ch1': [
            {'pdf_page': 97, 'start_line': 3, 'end_line': 8, 'status': 'rendered'},
            {'pdf_page': 98, 'start_line': 8, 'end_line': 13, 'status': 'rendered'},
            {'pdf_page': 99, 'start_line': None, 'end_line': None, 'status': 'empty'}]}
        path = self.root / '章节/ch1.md'
        path.parent.mkdir()
        path.write_text(self.text, encoding='utf-8', newline='')

    def read_json(self, name):
        return json.loads((self.root / name).read_text(encoding='utf-8'))

    def write_json(self, name, data):
        (self.root / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def chunks(self):
        return [json.loads(line) for line in (self.root / 'agent/chunks.jsonl').read_text(encoding='utf-8').splitlines()]

    def write_chunks(self, chunks):
        (self.root / 'agent/chunks.jsonl').write_text(
            ''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in chunks), encoding='utf-8')

    def generate(self):
        write_agent_index(self.root, self.chapters, self.pages, '../来源/源书.pdf')

    def test_index_is_opt_in_and_missing_manifest_does_not_write(self):
        before = list(self.root.rglob('*'))
        self.assertEqual(validate_agent_index(self.root), [])
        self.assertFalse((self.root / 'agent').exists())
        self.assertEqual(list(self.root.rglob('*')), before)
        self.generate()
        self.assertEqual(validate_agent_index(self.root), [])
        self.assertEqual({p.name for p in (self.root / 'agent').iterdir()},
                         {'chunks.jsonl', 'page-map.json', 'manifest.json'})
        manifest = self.read_json('agent/manifest.json')
        self.assertEqual(manifest['source_pdf'], '../来源/源书.pdf')
        self.assertEqual(manifest['source_pdf_base'], 'output_root')
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertEqual(self.read_json('agent/page-map.json'), self.pages)
        self.assertEqual(''.join(c['text'] for c in self.chunks()), self.text)

    def test_no_overwriting_existing_staging_files(self):
        (self.root / 'agent').mkdir()
        target = self.root / 'agent/page-map.json'
        target.write_text('人工文件', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(target.read_text(encoding='utf-8'), '人工文件')
        self.assertFalse((self.root / 'agent/manifest.json').exists())
        self.assertFalse((self.root / 'agent/chunks.jsonl').exists())

    def test_validation_catches_missing_duplicate_stale_and_mislabelled_chunks(self):
        self.generate()
        original = self.chunks()
        bad_text = copy.deepcopy(original); bad_text[-1]['text'] += '篡改'
        bad_page = copy.deepcopy(original); bad_page[-1]['pages'] = [1]
        bad_section = copy.deepcopy(original); bad_section[-1]['section'] = '错误节'
        gap = copy.deepcopy(original); gap[-1]['start_line'] += 1
        for variant in [original[1:], original[:-1], original + [original[0]],
                        bad_text, bad_page, bad_section, gap]:
            with self.subTest(chunks=variant):
                self.write_chunks(variant)
                self.assertTrue(validate_agent_index(self.root))
        self.write_chunks(original)
        self.assertEqual(validate_agent_index(self.root), [])

    def test_validation_catches_formula_split_even_when_reconstruction_matches(self):
        self.generate()
        lines = self.text.splitlines(keepends=True)
        chunks = self.chunks()
        base = chunks[-1]
        cut = 9
        first = {**base, 'id': 'part1', 'start_line': base['start_line'], 'end_line': cut,
                 'text': ''.join(lines[base['start_line'] - 1:cut]), 'pages': [97, 98]}
        second = {**base, 'id': 'part2', 'start_line': cut + 1,
                  'text': ''.join(lines[cut:]), 'pages': [98]}
        self.write_chunks(chunks[:-1] + [first, second])
        errors = validate_agent_index(self.root)
        self.assertTrue(any('splits' in error for error in errors), errors)
        self.assertEqual(''.join(c['text'] for c in self.chunks()), self.text)

    def test_incomplete_math_and_page_map_coverage_are_errors(self):
        path = self.root / '章节/ch1.md'
        path.write_text(self.text.replace('x=2', 'x=2 $$'), encoding='utf-8')
        self.generate()
        self.assertTrue(any('formula' in error for error in validate_agent_index(self.root)))
        path.write_text(self.text, encoding='utf-8')
        self.write_json('agent/page-map.json', {'ch1': []})
        self.assertTrue(any('page-map' in error for error in validate_agent_index(self.root)))

    def test_portable_source_and_document_paths(self):
        for source in ('C:/private/book.pdf', 'C:\\private\\book.pdf', '/private/book.pdf', '//host/book.pdf'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                write_agent_index(self.root, self.chapters, self.pages, source)
        for file in ('../escape.md', '/escape.md', 'C:/escape.md', 'x\\escape.md', 'agent/book.md'):
            with self.subTest(file=file), self.assertRaises(ValueError):
                write_agent_index(self.root, [{**self.chapters[0], 'file': file}], self.pages)
        self.assertFalse((self.root / 'agent').exists())
        self.generate()
        for field in ('chunks', 'page_map'):
            manifest = self.read_json('agent/manifest.json')
            original = copy.deepcopy(manifest)
            manifest[field] = '../outside.json'
            self.write_json('agent/manifest.json', manifest)
            self.assertTrue(validate_agent_index(self.root))
            self.write_json('agent/manifest.json', original)

    def test_validation_rejects_symlinked_chapter_when_supported(self):
        self.generate()
        with tempfile.TemporaryDirectory() as folder:
            external = Path(folder) / 'outside.md'
            external.write_text(self.text, encoding='utf-8')
            target = self.root / self.chapters[0]['file']
            target.unlink()
            try:
                target.symlink_to(external)
            except OSError:
                self.skipTest('Windows symlinks require permission')
            self.assertTrue(validate_agent_index(self.root))

    def test_crlf_staging_read_preserves_exact_text(self):
        text = self.text.replace('\n', '\r\n')
        (self.root / '章节/ch1.md').write_text(text, encoding='utf-8', newline='')
        self.generate()
        self.assertEqual(''.join(c['text'] for c in self.chunks()), text)
        self.assertEqual(validate_agent_index(self.root), [])


if __name__ == '__main__':
    unittest.main()
