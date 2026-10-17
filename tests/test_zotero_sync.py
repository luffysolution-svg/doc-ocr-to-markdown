import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
import sys
sys.path.insert(0, str(SCRIPTS))

from zotero_sync import (
    compute_metadata_diff,
    extract_frontmatter_and_content,
    extract_notes_section,
    markdown_to_zotero_html,
)


class ZoteroSyncTests(unittest.TestCase):
    def test_extract_notes_section(self):
        body = (
            '# 导读\n\n'
            '正文内容在这里。\n\n'
            '## 阅读笔记\n'
            '1. 这是一篇重要的逆向设计文献。\n'
            '2. 晶格参数预测精度达到 98%。\n\n'
            '## 参考文献\n'
            '[1] Nature, 2026.\n'
        )
        notes = extract_notes_section(body)
        self.assertIsNotNone(notes)
        self.assertIn('这是一篇重要的逆向设计文献', notes)
        self.assertNotIn('## 参考文献', notes)
        self.assertNotIn('正文内容在这里', notes)

    def test_markdown_to_zotero_html(self):
        md = '## 结论\n\n- 准确率提升\n- 速度加快\n\n> 核心亮点'
        res_html = markdown_to_zotero_html(md)
        self.assertIn('data-schema-version="9"', res_html)
        self.assertIn('<h2>结论</h2>', res_html)
        self.assertIn('<li>准确率提升</li>', res_html)
        self.assertIn('<blockquote>核心亮点</blockquote>', res_html)

    def test_compute_metadata_diff(self):
        fm = {
            'title': 'Corrected Title from PDF',
            'doi': '10.1038/s41586-025-0001',
            'date': '2025-03',
            'authors': ['Alice Smith', 'Bob Jones'],
            'abstract': 'Original abstract.',
        }
        zitem = {
            'data': {
                'title': 'Messy Scanned Title',
                'DOI': '10.1038/s41586-0000',
                'date': '2025-03',
                'creators': [
                    {'name': 'Alice Smith', 'creatorType': 'author'},
                    {'name': 'Bob Jones', 'creatorType': 'author'},
                ],
                'abstractNote': 'Original abstract.',
            }
        }
        diff = compute_metadata_diff(fm, zitem)
        self.assertIn('date', diff['identical'])
        self.assertIn('abstract', diff['identical'])
        self.assertIn('authors', diff['identical'])
        self.assertIn('title', diff['different'])
        self.assertEqual(diff['different']['title']['local'], 'Corrected Title from PDF')
        self.assertEqual(diff['different']['title']['zotero'], 'Messy Scanned Title')
        self.assertIn('doi', diff['different'])

    def test_extract_frontmatter_and_content(self):
        with tempfile.TemporaryDirectory() as td:
            md_path = Path(td) / 'test.md'
            md_path.write_text(
                '---\ntitle: "Test Paper"\nzotero_item_uri: "zotero://select/library/items/6DXKSD7N"\n---\n\n# Body\nText',
                encoding='utf-8'
            )
            meta, content = extract_frontmatter_and_content(md_path)
            self.assertEqual(meta['title'], 'Test Paper')
            self.assertEqual(meta['zotero_item_uri'], 'zotero://select/library/items/6DXKSD7N')
            self.assertIn('# Body', content)


if __name__ == '__main__':
    unittest.main()
