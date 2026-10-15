"""Local assembly validation and ownership-aware publishing (no OCR/network calls)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


OWNERSHIP = '.build-ownership.json'
RESERVED = {OWNERSHIP, '00-目录.md', 'build-report.json', 'images'}


def relative_name(value: str, *, component: bool = False) -> str:
    """Reject Windows and POSIX escape forms, even on the other platform."""
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'不安全的相对路径: {value!r}')
    parts = value.split('/')
    if any(p in ('', '.', '..') or ':' in p or re.search(r'[<>"|?*\x00-\x1f]', p)
           or p.endswith((' ', '.')) or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', p)
           for p in parts) or (component and len(parts) != 1):
        raise ValueError(f'不安全的相对路径: {value!r}')
    return value


def contained(root: Path, value: str) -> Path:
    value = relative_name(value)
    root = root.resolve()
    path = root / value
    # Reject symlinks as well as resolved escapes; do not follow user output links.
    current = root
    for part in Path(value).parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError(f'路径包含链接: {current}')
    if not path.resolve().is_relative_to(root):
        raise ValueError(f'路径逃逸: {path}')
    return path


def asset_path(root: Path, value: str) -> Path:
    # OCR paths commonly use Windows separators. Still reject drive/UNC/traversal.
    return contained(root, relative_name(value.replace('\\', '/')))


def read_items(folder: Path) -> tuple[list[dict], Path]:
    if not folder.is_dir():
        raise ValueError(f'缺少 OCR 分片目录: {folder}')
    lists = sorted(folder.rglob('*content_list.json'))
    if len(lists) != 1:
        raise ValueError(f'{folder}: 需要唯一 content_list.json，找到 {len(lists)} 个')
    path = contained(folder, lists[0].relative_to(folder).as_posix())
    items = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise ValueError(f'无效 OCR 列表: {path}')
    # Internal provenance is assigned here/from the manifest, never trusted from OCR.
    items = [{key: value for key, value in item.items() if not key.startswith('_')} for item in items]
    for item in items:
        pi = item.get('page_idx', 0)
        if type(pi) is not int or pi < 0:
            raise ValueError(f'OCR page_idx 越界: {pi!r} ({path})')
    return items, path.parent


def _integer(value, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f'{label} 必须为正整数: {value!r}')
    return value


def _ranges(entries: list[dict], total: int, start_key: str, end_key: str) -> None:
    if not isinstance(entries, list) or not entries:
        raise ValueError('页码范围列表不能为空')
    cursor = 1
    ids = set()
    for entry in sorted(entries, key=lambda e: _integer(e[start_key], start_key)):
        identity = relative_name(entry['id'], component=True)
        if identity in ids:
            raise ValueError(f'重复 id: {identity}')
        ids.add(identity)
        start = _integer(entry[start_key], start_key)
        end = _integer(entry[end_key], end_key)
        if start != cursor or end < start or end > total:
            raise ValueError(f'页码范围重叠、遗漏或越界: {identity} {start}-{end}，期望起页 {cursor}')
        cursor = end + 1
    if cursor != total + 1:
        raise ValueError(f'页码范围遗漏: 尚未覆盖 {cursor}-{total}')


def load_book(raw_root: Path, manifest: dict, sections: list[dict], pdf_path: Path | None) -> list[dict]:
    total = _integer(manifest['pages'], 'manifest pages')
    _ranges(manifest['parts'], total, 'start_page', 'end_page')
    _ranges(sections, total, 'start', 'end')
    filenames = set()
    for sec in sections:
        filename = relative_name(sec.get('file', f"{sec['id']}.md"))
        if Path(filename).suffix.lower() != '.md' or filename.split('/')[0] in RESERVED:
            raise ValueError(f'章节文件名保留或无效: {filename}')
        if filename.casefold() in filenames:
            raise ValueError(f'章节文件名重复: {filename}')
        filenames.add(filename.casefold())
        if not isinstance(sec.get('title'), str) or not sec['title'].strip():
            raise ValueError(f'章节标题不能为空: {sec["id"]}')
    if pdf_path:
        import fitz
        with fitz.open(pdf_path) as pdf:
            if len(pdf) != total:
                raise ValueError(f'PDF 页数与 manifest 不符: {len(pdf)} != {total}')
    blocks = []
    for part in sorted(manifest['parts'], key=lambda p: p['start_page']):
        count = part['end_page'] - part['start_page'] + 1
        if 'pages' in part and (type(part['pages']) is not int or part['pages'] != count):
            raise ValueError(f'分片页数不一致: {part["id"]}')
        folder = contained(raw_root, part['id'])
        items, ocr_dir = read_items(folder)
        for item in items:
            pi = item.get('page_idx', 0)
            if pi >= count:
                raise ValueError(f'OCR page_idx 越界: {part["id"]} page_idx={pi}, pages={count}')
            blocks.append({**item, '_page': pi + part['start_page'], '_ocr_dir': str(ocr_dir)})
    return blocks


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Publisher:
    """Preflight every collision before writing; never delete existing assets.

    Unmodified owned files may be regenerated. --overwrite permits changes to owned
    files edited since the previous run, never adoption of an unowned user file.
    Stale owned files are retained intentionally, including on --only runs.
    """

    def __init__(self, root: Path, overwrite: bool = False):
        self.root = root
        self.overwrite = overwrite
        self.owned = {}
        if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
            raise ValueError(f'输出目录不能是链接: {root}')
        ledger = contained(root, OWNERSHIP)
        if ledger.exists():
            data = json.loads(ledger.read_text(encoding='utf-8'))
            if data.get('version') != 1 or not isinstance(data.get('files'), dict):
                raise ValueError('所有权清单格式无效')
            self.owned = data['files']
            for name, sha in self.owned.items():
                contained(root, name)
                if name == OWNERSHIP or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                    raise ValueError('所有权清单条目无效')

    def check(self, name: str) -> Path:
        path = contained(self.root, name)
        if path.exists():
            if not path.is_file() or name not in self.owned:
                raise ValueError(f'拒绝覆盖非生成文件: {path}')
            if digest(path.read_bytes()) != self.owned[name] and not self.overwrite:
                raise ValueError(f'生成文件有人工修改，需 --overwrite: {path}')
        return path

    def previous_report(self) -> dict:
        path = self.check('build-report.json')
        if not path.exists():
            return {'chapters': [], 'review': {}}
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data.get('chapters'), list) or not isinstance(data.get('review', {}), dict):
            raise ValueError('build-report.json 格式无效')
        return data

    def publish(self, stage: Path) -> None:
        files = {p.relative_to(stage).as_posix(): p.read_bytes()
                 for p in stage.rglob('*') if p.is_file()}
        targets = {name: self.check(name) for name in files}
        # Case-folded names also protect outputs when generated on Windows.
        names = list(files)
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError('输出路径大小写冲突')
        for name, path in targets.items():
            for parent in path.parents:
                if parent == self.root:
                    break
                if parent.exists() and not parent.is_dir():
                    raise ValueError(f'输出父路径不是目录: {parent}')
            if any(other.startswith(name + '/') for other in files):
                raise ValueError(f'输出文件与目录冲突: {name}')
        self.root.mkdir(parents=True, exist_ok=True)
        for name, path in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
            self.owned[name] = digest(files[name])
        contained(self.root, OWNERSHIP).write_text(
            json.dumps({'version': 1, 'files': self.owned}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
