"""Local assembly validation and ownership-aware publishing (no OCR/network calls)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from portable_paths import safe_path, walk_files


OWNERSHIP = '.build-ownership.json'
RESERVED = {OWNERSHIP, '00-目录.md', 'build-report.json', 'images', 'agent'}


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
    if not safe_path(folder).is_dir():
        raise ValueError(f'缺少 OCR 分片目录: {folder}')
    lists = sorted([p for p in walk_files(folder, '*content_list.json') if not p.name.endswith('_v2.json')])
    if len(lists) != 1:
        raise ValueError(f'{folder}: 需要唯一 content_list.json，找到 {len(lists)} 个')
    path = contained(folder, lists[0].relative_to(folder).as_posix())
    items = json.loads(safe_path(path).read_text(encoding='utf-8'))
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
        if (Path(filename).suffix.lower() != '.md'
                or filename.split('/')[0].casefold() in {name.casefold() for name in RESERVED}):
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

    def __init__(self, root: Path, overwrite: bool = False, state_root: Path | None = None):
        self.root = root
        self.state_root = state_root if state_root is not None else root.parent / f'.{root.name}.build'
        if (self.state_root.resolve().is_relative_to(root.resolve())
                or root.resolve().is_relative_to(self.state_root.resolve())):
            raise ValueError('构建状态与交付目录不得相互包含')
        if self.state_root.is_symlink() or (hasattr(self.state_root, 'is_junction') and self.state_root.is_junction()):
            raise ValueError('构建状态目录不能是链接')
        self.overwrite = overwrite
        self.owned = {}
        if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
            raise ValueError(f'输出目录不能是链接: {root}')
        ledger = contained(self.state_root, OWNERSHIP)
        if not safe_path(ledger).exists():
            ledger = contained(root, OWNERSHIP)
        if safe_path(ledger).exists():
            data = json.loads(safe_path(ledger).read_text(encoding='utf-8'))
            if data.get('version') != 1 or not isinstance(data.get('files'), dict):
                raise ValueError('所有权清单格式无效')
            self.owned = data['files']
            for name, sha in self.owned.items():
                contained(root, name)
                if name == OWNERSHIP or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                    raise ValueError('所有权清单条目无效')

    def check(self, name: str) -> Path:
        path = contained(self.root, name)
        if safe_path(path).exists():
            if not safe_path(path).is_file() or name not in self.owned:
                raise ValueError(f'拒绝覆盖非生成文件: {path}')
            if digest(safe_path(path).read_bytes()) != self.owned[name] and not self.overwrite:
                raise ValueError(f'生成文件有人工修改，需 --overwrite: {path}')
        return path

    def previous_report(self) -> dict:
        path = contained(self.state_root, 'build-report.json')
        if not safe_path(path).exists():
            path = self.check('build-report.json')
        if not safe_path(path).exists():
            return {'chapters': [], 'review': {}}
        data = json.loads(safe_path(path).read_text(encoding='utf-8'))
        if not isinstance(data.get('chapters'), list) or not isinstance(data.get('review', {}), dict):
            raise ValueError('build-report.json 格式无效')
        return data

    def publish(self, stage: Path) -> None:
        report_path = stage / 'build-report.json'
        report_data = safe_path(report_path).read_bytes() if safe_path(report_path).exists() else None
        files = {p.relative_to(stage).as_posix(): safe_path(p).read_bytes()
                 for p in walk_files(stage) if safe_path(p).is_file() and p != report_path}
        # Check every ancestor before writing delivery files, including ancestors
        # above a missing state_root (Path.exists() alone cannot detect those).
        for directory in (self.root, self.state_root):
            for parent in (directory, *directory.parents):
                if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
                    raise ValueError(f'路径包含链接: {parent}')
                if safe_path(parent).exists() and not safe_path(parent).is_dir():
                    raise ValueError(f'父路径不是目录: {parent}')
        state_targets = {name: contained(self.state_root, name)
                         for name in (OWNERSHIP, 'build-report.json')}
        for state_file in state_targets.values():
            if safe_path(state_file).exists() and not safe_path(state_file).is_file():
                raise ValueError(f'构建状态不是文件: {state_file}')
        targets = {name: self.check(name) for name in files}
        # Case-folded names also protect outputs when generated on Windows.
        names = {name.casefold() for name in files}
        if len(names) != len(files):
            raise ValueError('输出路径大小写冲突')
        for name, path in targets.items():
            for parent in path.parents:
                if safe_path(parent).exists() and not safe_path(parent).is_dir():
                    raise ValueError(f'输出父路径不是目录: {parent}')
            if any(other.startswith(name.casefold() + '/') for other in names):
                raise ValueError(f'输出文件与目录冲突: {name}')

        owned = {**self.owned, **{name: digest(data) for name, data in files.items()}}
        writes = [(targets[name], data) for name, data in files.items()]
        if report_data is not None:
            writes.append((state_targets['build-report.json'], report_data))
        ledger_data = (json.dumps({'version': 1, 'files': owned},
                                  ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        writes.append((state_targets[OWNERSHIP], ledger_data))
        # Keep just the required original bytes; staged payloads above are reused.
        originals = {path: safe_path(path).read_bytes() if safe_path(path).exists() else None for path, _ in writes}
        attempted = []
        created_dirs = []

        def make_parents(directory: Path) -> None:
            missing = []
            while not safe_path(directory).exists():
                missing.append(directory)
                directory = directory.parent
            for directory in reversed(missing):
                safe_path(directory).mkdir()
                created_dirs.append(directory)

        def replace_bytes(path: Path, data: bytes) -> None:
            fd, temporary = tempfile.mkstemp(prefix='.tmp.', suffix='.tmp', dir=safe_path(path.parent))
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.replace(safe_path(temporary), safe_path(path))
            finally:
                try:
                    os.unlink(safe_path(temporary))
                except OSError:
                    pass

        try:
            for path, data in writes:
                make_parents(path.parent)
                attempted.append(path)
                replace_bytes(path, data)
        except BaseException as error:
            rollback_errors = []
            for path in reversed(attempted):
                try:
                    if originals[path] is None:
                        try:
                            os.unlink(safe_path(path))
                        except OSError:
                            pass
                    else:
                        replace_bytes(path, originals[path])
                except BaseException as rollback_error:
                    rollback_errors.append(rollback_error)
            for directory in reversed(created_dirs):
                try:
                    safe_path(directory).rmdir()  # Only our own empty directories; never recurse.
                except OSError:
                    pass
            if rollback_errors:
                raise RuntimeError(f'发布失败且部分文件恢复失败: {rollback_errors}') from error
            raise
        self.owned = owned
