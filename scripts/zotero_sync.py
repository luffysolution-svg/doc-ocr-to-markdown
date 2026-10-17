#!/usr/bin/env python
"""Zotero bidirectional synchronization tool.

Capabilities:
1. Metadata Sync: Compares Markdown frontmatter (title, authors, DOI, date, etc.)
   with Zotero local API items, generates field diffs, and safely updates Zotero
   when --apply is specified (defaults to read-only --dry-run).
2. Notes & Annotations Sync:
   - Pushes user/agent reading notes (under '## 阅读笔记') from Markdown to Zotero
     as rich-text child notes.
   - Pulls PDF annotations (highlights, comments) from Zotero into Markdown.
3. Attachments Audit: Verifies attachment states (primary PDF, supplemental PDFs).
4. Emits detailed sync audit report JSON.

Usage:
    python scripts/zotero_sync.py <markdown_path> [--apply] [--pull-annotations] [--report sync-report.json]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from console_output import safe_print as print

DEFAULT_BASE_URL = 'http://127.0.0.1:23119/api/users/0'
KEY_PATTERN = re.compile(r'[A-Z0-9]{8}\Z')
URI_PATTERN = re.compile(r'zotero://select/(?:library|groups/\d+)/items/(?P<key>[A-Z0-9]{8})')

FIELD_MAPPING = {
    'title': 'title',
    'publication_title': 'publicationTitle',
    'volume': 'volume',
    'issue': 'issue',
    'pages': 'pages',
    'date': 'date',
    'doi': 'DOI',
    'abstract': 'abstractNote',
    'url': 'url',
    'language': 'language',
}


def extract_frontmatter_and_content(md_path: Path) -> tuple[dict, str]:
    """Parse YAML frontmatter and body content from a markdown file."""
    text = md_path.read_text(encoding='utf-8')
    if not text.startswith('---'):
        return {}, text
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}, text
    import yaml
    try:
        data = yaml.safe_load(parts[1])
        meta = data if isinstance(data, dict) else {}
    except Exception:
        meta = {}
    return meta, parts[2]


def extract_notes_section(content: str) -> str | None:
    """Extract reading notes section from markdown body."""
    headers = ('## 阅读笔记', '## 读书笔记', '## 核心结论', '## 笔记', '## Notes')
    lines = content.splitlines()
    capturing = False
    captured_lines = []
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(h) for h in headers):
            capturing = True
            continue
        elif capturing:
            if stripped.startswith('## ') and not any(stripped.startswith(h) for h in headers):
                break
            captured_lines.append(line)
    res = '\n'.join(captured_lines).strip()
    return res if res else None


def markdown_to_zotero_html(md_text: str) -> str:
    """Convert simple markdown notes to Zotero compatible HTML."""
    paragraphs = []
    for para in md_text.split('\n\n'):
        para = para.strip()
        if not para:
            continue
        if para.startswith('- ') or para.startswith('* '):
            items = [f'<li>{html.escape(item.strip("- *"))}</li>' for item in para.split('\n') if item.strip()]
            paragraphs.append(f'<ul>{"".join(items)}</ul>')
        elif para.startswith('### '):
            paragraphs.append(f'<h3>{html.escape(para[4:].strip())}</h3>')
        elif para.startswith('## '):
            paragraphs.append(f'<h2>{html.escape(para[3:].strip())}</h2>')
        elif para.startswith('> '):
            paragraphs.append(f'<blockquote>{html.escape(para[2:].strip())}</blockquote>')
        else:
            paragraphs.append(f'<p>{html.escape(para)}</p>')
    body = ''.join(paragraphs)
    return f'<div data-schema-version="9">{body}</div>'


class ZoteroSyncClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 15):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def _request(self, method: str, endpoint: str, data: Any = None, headers: dict | None = None) -> Any:
        url = f'{self.base_url}/{endpoint.lstrip("/")}'
        req_headers = {'Accept': 'application/json'}
        if headers:
            req_headers.update(headers)
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            req_headers['Content-Type'] = 'application/json; charset=utf-8'
        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as err:
            err_msg = err.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'Zotero API {method} {url} failed (HTTP {err.code}): {err_msg}')
        except Exception as exc:
            raise RuntimeError(f'Zotero API connection error: {exc}')

    def get_item(self, item_key: str) -> dict:
        return self._request('GET', f'items/{item_key}')

    def get_children(self, item_key: str) -> list[dict]:
        res = self._request('GET', f'items/{item_key}/children')
        return res if isinstance(res, list) else []

    def update_item_metadata(self, item_key: str, version: int, patch_data: dict) -> dict:
        """Update item fields via PATCH with version check."""
        headers = {'If-Unmodified-Since-Version': str(version)}
        # In Zotero local API, payload can be patch dict or full item data
        return self._request('PATCH', f'items/{item_key}', data=patch_data, headers=headers)

    def add_child_note(self, item_key: str, note_html: str, tags: list[str] | None = None) -> dict:
        """Create a child note under the given parent item."""
        tag_objs = [{'tag': t} for t in (tags or ['KnowledgeBase-Sync'])]
        payload = [{
            'itemType': 'note',
            'parentItem': item_key,
            'note': note_html,
            'tags': tag_objs,
        }]
        return self._request('POST', 'items', data=payload)


def compute_metadata_diff(frontmatter: dict, zotero_item: dict) -> dict:
    """Compare local Markdown frontmatter against Zotero item data."""
    zdata = zotero_item.get('data', zotero_item)
    diff = {
        'identical': {},
        'different': {},
        'local_only': {},
        'zotero_only': {},
    }

    # Text fields
    for md_key, z_key in FIELD_MAPPING.items():
        md_val = frontmatter.get(md_key)
        z_val = zdata.get(z_key)
        if md_val is not None:
            md_str = str(md_val).strip()
            z_str = str(z_val or '').strip()
            if z_key == 'DOI':
                md_str = md_str.lower().replace('https://doi.org/', '').replace('http://doi.org/', '').replace('doi:', '')
                z_str = z_str.lower().replace('https://doi.org/', '').replace('http://doi.org/', '').replace('doi:', '')
            if md_str == z_str:
                diff['identical'][md_key] = md_str
            elif z_str == '':
                diff['local_only'][md_key] = md_str
            else:
                diff['different'][md_key] = {'local': md_str, 'zotero': z_str}
        elif z_val:
            diff['zotero_only'][md_key] = z_val

    # Authors / Creators
    md_authors = frontmatter.get('authors')
    z_creators = zdata.get('creators', [])
    if md_authors and isinstance(md_authors, list):
        z_authors = []
        for c in z_creators:
            name = c.get('name') or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            if name:
                z_authors.append(name)
        if md_authors == z_authors:
            diff['identical']['authors'] = md_authors
        else:
            diff['different']['authors'] = {'local': md_authors, 'zotero': z_authors}

    return diff


def sync_document(md_path: Path, client: ZoteroSyncClient, apply: bool = False,
                  pull_annotations: bool = False) -> dict:
    """Perform bidirectional sync analysis and optional execution on a single document."""
    md_path = md_path.resolve()
    meta, content = extract_frontmatter_and_content(md_path)
    uri = meta.get('zotero_item_uri', '')
    m = URI_PATTERN.search(uri)
    if not m:
        raise ValueError(f'{md_path.name}: frontmatter missing valid zotero_item_uri')
    item_key = m.group('key')

    # 1. Fetch Zotero Item
    zotero_item = client.get_item(item_key)
    zdata = zotero_item.get('data', zotero_item)
    version = zotero_item.get('version', zdata.get('version', 0))

    # 2. Diff Metadata
    diff = compute_metadata_diff(meta, zotero_item)

    # 3. Children (Attachments & Notes)
    children = client.get_children(item_key)
    attachments = [c for c in children if c.get('data', {}).get('itemType') == 'attachment']
    notes = [c for c in children if c.get('data', {}).get('itemType') == 'note']

    # 4. Reading Notes check
    local_notes = extract_notes_section(content)
    notes_synced = False
    metadata_updated = False
    annotations_pulled = 0

    patch_payload = {}
    for k, v in diff['different'].items():
        if k in FIELD_MAPPING:
            patch_payload[FIELD_MAPPING[k]] = v['local']
    for k, v in diff['local_only'].items():
        if k in FIELD_MAPPING:
            patch_payload[FIELD_MAPPING[k]] = v

    if apply and patch_payload:
        try:
            client.update_item_metadata(item_key, version, patch_payload)
            metadata_updated = True
        except Exception as exc:
            patch_payload['error'] = str(exc)

    if apply and local_notes:
        note_html = markdown_to_zotero_html(local_notes)
        # Check if already present to avoid duplication
        exists = any(local_notes[:40] in n.get('data', {}).get('note', '') for n in notes)
        if not exists:
            try:
                client.add_child_note(item_key, note_html)
                notes_synced = True
            except Exception as exc:
                notes_synced = f'Error: {exc}'

    # 5. Pull annotations from attachments
    pulled_annotations_data = []
    if pull_annotations:
        for att in attachments:
            att_key = att.get('data', {}).get('key')
            if not att_key:
                continue
            ann_children = client.get_children(att_key)
            for ann in ann_children:
                adata = ann.get('data', {})
                if adata.get('itemType') == 'annotation':
                    pulled_annotations_data.append({
                        'text': adata.get('annotationText', ''),
                        'comment': adata.get('annotationComment', ''),
                        'color': adata.get('annotationColor', ''),
                        'page': adata.get('annotationPageLabel', ''),
                    })
        if pulled_annotations_data:
            annotations_pulled = len(pulled_annotations_data)
            # Append annotations section to markdown if not present
            if apply and '## Zotero 批注与高亮' not in content:
                ann_lines = ['\n\n## Zotero 批注与高亮\n']
                for ann in pulled_annotations_data:
                    p = f"(Page {ann['page']})" if ann['page'] else ''
                    if ann['text']:
                        ann_lines.append(f"> {ann['text']} {p}\n")
                    if ann['comment']:
                        ann_lines.append(f"- **批注**: {ann['comment']}\n")
                updated_text = md_path.read_text(encoding='utf-8') + ''.join(ann_lines)
                md_path.write_text(updated_text, encoding='utf-8')

    report = {
        'file': md_path.name,
        'item_key': item_key,
        'item_title': zdata.get('title'),
        'apply_mode': apply,
        'metadata_diff': diff,
        'metadata_updated': metadata_updated,
        'patch_attempted': patch_payload if apply else None,
        'attachments_count': len(attachments),
        'attachments': [
            {'key': a.get('data', {}).get('key'), 'title': a.get('data', {}).get('title'),
             'filename': a.get('data', {}).get('filename')}
            for a in attachments
        ],
        'notes_in_zotero_count': len(notes),
        'local_notes_detected': bool(local_notes),
        'notes_synced_to_zotero': notes_synced,
        'annotations_pulled': annotations_pulled,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', help='Markdown 文件路径或知识库目录')
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL, help='Zotero 本地 API 基础 URL')
    parser.add_argument('--apply', action='store_true', help='执行实际回写（默认仅 dry-run 预览 diff）')
    parser.add_argument('--pull-annotations', action='store_true', help='从 Zotero PDF 拉取高亮批注至 Markdown')
    parser.add_argument('--report', help='输出详细同步报告 JSON 路径')
    args = parser.parse_args()

    client = ZoteroSyncClient(base_url=args.base_url)
    target = Path(args.path).resolve()
    files = [target] if target.is_file() else sorted(target.rglob('*.md'))
    files = [f for f in files if f.name != '00-目录.md']

    reports = []
    print('=' * 60)
    print(f'Zotero 双向同步审查：目标文件 {len(files)} 个，模式: {"APPLY (写入)" if args.apply else "DRY-RUN (只读对比)"}')
    print('=' * 60)

    for f in files:
        try:
            rep = sync_document(f, client, apply=args.apply, pull_annotations=args.pull_annotations)
            reports.append(rep)
            diff_count = len(rep['metadata_diff']['different']) + len(rep['metadata_diff']['local_only'])
            print(f"[{rep['item_key']}] {f.name}: 字段差异 {diff_count} 项, 附件 {rep['attachments_count']} 个, "
                  f"笔记已同步: {rep['notes_synced_to_zotero']}, 批注拉取: {rep['annotations_pulled']}")
        except Exception as exc:
            print(f"[-] {f.name}: 跳过或失败 ({exc})")

    if args.report:
        out_r = Path(args.report).resolve()
        out_r.parent.mkdir(parents=True, exist_ok=True)
        out_r.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'详细同步报告已保存至: {out_r}')


if __name__ == '__main__':
    main()
