"""Read-only Zotero local API export; no storage discovery or library writes.

export_library(out, base_url=DEFAULT_BASE_URL, items=None) returns the same list
of metadata records written to library.json. ids.tsv contains path<TAB>stable ID
for ready PDFs only. Existing files are immutable unless bytes are identical.
"""
from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPException
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, url2pathname

DEFAULT_BASE_URL = 'http://127.0.0.1:23119/api/users/0'
PAPER_TYPES = frozenset({'journalArticle', 'conferencePaper', 'preprint', 'report',
                         'thesis', 'manuscript'})
KEY = re.compile(r'[A-Z0-9]{8}\Z')


class ExportError(ValueError):
    """Invalid API response or a conflicting export destination."""


def validate_base_url(value: str) -> str:
    """Accept only a credential-free loopback Zotero API (or server root)."""
    if any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ExportError('Invalid loopback API URL')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ExportError('API URL must be loopback HTTP(S) without credentials, query or fragment')
    host = parsed.hostname
    if host == 'localhost':
        host = '127.0.0.1'  # Do not depend on DNS or send requests outside loopback.
    else:
        try:
            address = ipaddress.ip_address(host or '')
        except ValueError as exc:
            raise ExportError('API URL must use localhost or a loopback IP') from exc
        if not address.is_loopback or '%' in (host or ''):
            raise ExportError('API URL must use a loopback IP')
    port = parsed.port  # Also validates the port before any I/O.
    api_path = parsed.path.rstrip('/') or '/api/users/0'
    if not re.fullmatch(r'/api/(?:users|groups)/\d+', api_path):
        raise ExportError('API URL path must identify /api/users/ID or /api/groups/ID')
    authority = f'[{host}]' if ':' in host else host
    if port is not None:
        authority += f':{port}'
    return f'{parsed.scheme}://{authority}{api_path}'


def valid_key(value: str) -> str:
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise ExportError('Invalid Zotero item key')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalAPI:
    def __init__(self, base_url=DEFAULT_BASE_URL, timeout=30):
        self.base_url = validate_base_url(base_url)
        self.timeout = timeout
        # urllib does not load netrc; no proxy, cookie jar or credential handlers.
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def get(self, endpoint, params=None):
        if not re.fullmatch(r'/items(?:/top|/[A-Z0-9]{8}(?:/children|/file)?)?', endpoint):
            raise ExportError('Invalid local API endpoint')
        url = self.base_url + endpoint
        if params:
            url += '?' + urlencode(params)
        request = Request(url, headers={'Accept': 'application/json'}, method='GET')
        try:
            return self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            if exc.code == 302 and endpoint.endswith('/file'):
                return exc
            exc.close()
            raise ExportError(f'Local API returned HTTP {exc.code} for {endpoint}') from None

    def items(self, endpoint, params=None):
        """Offset pagination never follows API-provided external next links."""
        start, limit, seen = 0, 100, set()
        snapshot = None
        while True:
            with self.get(endpoint, {**(params or {}), 'start': start, 'limit': limit}) as response:
                batch = json.load(response)
                total = response.headers.get('Total-Results')
                current = (response.headers.get('Last-Modified-Version'), total)
                if snapshot is None:
                    snapshot = current
                elif snapshot != current:
                    raise ExportError('API changed during pagination; retry a complete snapshot')
            if not isinstance(batch, list):
                raise ExportError('Expected an API item list')
            for item in batch:
                data = _data(item)
                key = valid_key(item.get('key') or data.get('key'))
                if key in seen:
                    raise ExportError('Repeated item in pagination; refusing incomplete snapshot')
                seen.add(key)
                yield item
            start += len(batch)
            if not batch:
                if total is not None and start < int(total):
                    raise ExportError('Truncated API pagination')
                break
            if total is not None and start >= int(total):
                break
            # Without Total-Results continue until empty, even for capped pages.

    def pdf(self, attachment_key):
        with self.get(f'/items/{valid_key(attachment_key)}/file') as response:
            if response.status == 302:
                source = local_file_path(response.headers.get('Location', ''))
                # Open precisely the API-selected file; never inspect its directory.
                with source.open('rb') as handle:
                    raw = handle.read()
            elif response.status == 200:
                raw = response.read()
            else:
                raise ExportError('Unsupported attachment response')
        if not raw.startswith(b'%PDF-'):
            raise ExportError('Attachment does not have PDF magic')
        return raw


def local_file_path(location: str) -> Path:
    parsed = urlsplit(location)
    if (parsed.scheme != 'file' or parsed.netloc or parsed.query or parsed.fragment
            or any(ord(c) < 32 for c in location)):
        raise ExportError('Attachment redirect must be a local file URL without a network authority')
    # url2pathname already percent-decodes: a second unquote corrupts literal %xx.
    decoded = url2pathname(parsed.path)
    if ('\x00' in decoded or decoded.replace('\\', '/').startswith('//')
            or decoded.startswith(('\\', '/\\'))):
        raise ExportError('Network or device file paths are forbidden')
    path = Path(decoded)
    if not path.is_absolute():
        raise ExportError('Attachment file path must be absolute')
    return path


def _check_destination(path: Path, raw: bytes) -> bool:
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ExportError(f'Export destination contains a link: {parent}')
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ExportError(f'Existing export differs; refusing overwrite: {path}')
        return True
    return False


def _atomic_write(path: Path, raw: bytes):
    """Publish a complete file atomically, without replacing any existing bytes."""
    if _check_destination(path, raw):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.zotero-', suffix='.tmp', delete=False) as handle:
            name = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # Unlike replace(), link() refuses a destination created concurrently.
        try:
            os.link(name, path)
        except FileExistsError:
            if not _check_destination(path, raw):
                raise
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_owned(path: Path, owner: str):
    if not path.exists():
        return None
    _check_destination(path, path.read_bytes())
    obj = json.loads(path.read_text(encoding='utf-8'))
    if (obj.get('owner') != owner or obj.get('sha256') != sha256(
            json.dumps(obj.get('payload'), ensure_ascii=False, sort_keys=True).encode())):
        raise ExportError(f'Unowned or manually edited state/report: {path}')
    return obj['payload']


def replace_checked(path: Path, raw: bytes, expected: bytes | None):
    """Atomic publication with a cooperative lock and a last ownership check."""
    if expected is None:
        _atomic_write(path, raw)
        return
    _check_destination(path, expected)
    if raw == expected:
        return
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.zotero-', delete=False) as handle:
            name = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if not path.exists() or path.read_bytes() != expected:
            raise ExportError(f'Concurrent file modification: {path}')
        _check_destination(path, expected)
        os.replace(name, path)
    finally:
        if name:
            name.unlink(missing_ok=True)


def write_owned(path: Path, payload, owner: str):
    previous = path.read_bytes() if path.exists() else None
    read_owned(path, owner)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    raw = (json.dumps({'owner': owner, 'sha256': sha256(canonical), 'payload': payload},
                      ensure_ascii=False, indent=2) + '\n').encode()
    replace_checked(path, raw, previous)


class FileLock:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _check_destination(self.path, b'')
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ExportError(f'Another operation owns lock: {self.path}') from None
        return self

    def __exit__(self, *args):
        os.close(self.fd)
        self.path.unlink()


def library_identity(api: LocalAPI, item: dict) -> dict:
    """Validate response identity, allowing /users/0 only as an explicit alias."""
    match = re.search(r'/api/(users|groups)/(\d+)$', api.base_url)
    kind, number = ('user' if match[1] == 'users' else 'group'), int(match[2])
    actual = item.get('library')
    if not isinstance(actual, dict) or actual.get('type') != kind:
        raise ExportError('API response library type mismatch')
    ident = actual.get('id')
    if not str(ident).isdigit() or (number != int(ident) and not (kind == 'user' and number == 0)):
        raise ExportError('API response library ID mismatch')
    return {'type': kind, 'id': int(ident)}


def _data(item):
    if not isinstance(item, dict) or not isinstance(item.get('data'), dict):
        raise ExportError('Invalid Zotero item metadata')
    return item['data']


def export_library(out, base_url=DEFAULT_BASE_URL, items=None, *, timeout=30, refresh=False):
    root = Path(os.path.abspath(out))
    for parent in (root, *root.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ExportError('Export root must not contain links')
    with FileLock(root.parent / f'.{root.name}.zotero-export.lock'):
        return _export_library(root, base_url, items, timeout=timeout, refresh=refresh)


def _export_library(out, base_url=DEFAULT_BASE_URL, items=None, *, timeout=30, refresh=False):
    """Export paper attachments, retaining no_pdf/error/duplicate alias records.

    IDs are item keys, with an attachment suffix for papers having multiple PDFs.
    Only ready records enter ids.tsv; aliases point at their canonical copied PDF.
    API/listing errors abort; paper/attachment failures are isolated in records.
    """
    api = LocalAPI(base_url, timeout=timeout)
    wanted = None if items is None else {valid_key(key) for key in items}
    root = Path(os.path.abspath(out))
    # Fail before API access if the output is a linked directory.
    for parent in (root, *root.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ExportError('Export root must not contain links')
    owner = f'zotero-export:{api.base_url}'
    manifest = root / '.zotero-export-state.json'
    managed = read_owned(manifest, owner) or {}
    # Check every owned output before any fetch or publication.
    for name, digest in managed.items():
        if name not in ('library.json', 'ids.tsv'):
            raise ExportError('Invalid managed manifest path')
        path = root / name
        if not path.is_file() or sha256(path.read_bytes()) != digest:
            raise ExportError(f'Existing export differs; refusing overwrite: {path}')
        _check_destination(path, path.read_bytes())
    records, seen, found = [], {}, set()
    # Materialize pagination first so listing failure cannot leave a partial batch.
    top = list(api.items('/items/top'))
    for item in sorted(top, key=lambda obj: str(obj.get('key', '')) if isinstance(obj, dict) else ''):
        key = item.get('key') if isinstance(item, dict) else None
        if wanted is not None and key not in wanted:
            continue
        record = {'item_key': key, 'metadata': item}
        try:
            valid_key(key)
            found.add(key)
            record['library'] = library_identity(api, item)
            data = _data(item)
            if data.get('deleted') or data.get('itemType') not in PAPER_TYPES:
                continue
            record['title'] = data.get('title', '')
            children = list(api.items(f'/items/{key}/children'))
            pdfs = [child for child in children if _data(child).get('itemType') == 'attachment'
                    and not _data(child).get('deleted')
                    and _data(child).get('contentType', '').lower() == 'application/pdf']
            if not pdfs:
                records.append({**record, 'status': 'no_pdf'})
                continue
        except (OSError, ValueError, TypeError, HTTPException) as exc:
            records.append({**record, 'status': 'error', 'error': str(exc)})
            continue
        for attachment in sorted(pdfs, key=lambda obj: str(obj.get('key', ''))):
            attachment_key = attachment.get('key')
            rec = {**record, 'attachment_key': attachment_key, 'attachment': attachment}
            try:
                valid_key(attachment_key)
                rec['id'] = key if len(pdfs) == 1 else f'{key}-{attachment_key}'
                raw = api.pdf(attachment_key)
                digest = hashlib.sha256(raw).hexdigest()
                rec['sha256'] = digest
                if digest in seen:
                    canonical = seen[digest]
                    rec.update(status='duplicate_bytes', path=canonical['path'],
                               duplicate_of=canonical['item_key'], duplicate_of_id=canonical['id'])
                else:
                    dest = root / 'sources' / f'{key}-{attachment_key}.pdf'
                    _atomic_write(dest, raw)
                    rec.update(status='ready', path=str(dest))
                    seen[digest] = rec
            except (OSError, ValueError, TypeError, HTTPException) as exc:
                rec.update(status='error', error=str(exc))
            records.append(rec)
    for key in sorted((wanted or set()) - found):
        records.append({'item_key': key, 'status': 'error', 'error': 'Requested item not found in top-level API results'})
    library = (json.dumps(records, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    ids = ''.join(f'{rec["path"]}\t{rec["id"]}\n' for rec in records if rec['status'] == 'ready').encode('utf-8')
    outputs = [(root / 'library.json', library), (root / 'ids.tsv', ids)]
    # Detect either manifest conflict before publishing either manifest.
    previous = {}
    for path, raw in outputs:
        if refresh and path.name in managed:
            previous[path] = path.read_bytes()
            _check_destination(path, previous[path])
        else:
            _check_destination(path, raw)
            previous[path] = None
    for path, raw in outputs:
        replace_checked(path, raw, previous[path])
    write_owned(manifest, {path.name: sha256(raw) for path, raw in outputs}, owner)
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, help='Export work directory')
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL, help='Loopback Zotero API or server root')
    parser.add_argument('--items', nargs='+', help='Optional Zotero top-level item keys')
    parser.add_argument('--refresh', action='store_true', help='Refresh only hash-verified managed manifests')
    args = parser.parse_args(argv)
    try:
        records = export_library(args.out, args.base_url, args.items, refresh=args.refresh)
    except (OSError, ValueError, HTTPException) as exc:
        print(f'Export failed: {exc}', file=sys.stderr)
        return 1
    counts = {status: sum(rec['status'] == status for rec in records)
              for status in ('ready', 'duplicate_bytes', 'no_pdf', 'error')}
    print(json.dumps(counts, ensure_ascii=False))
    return 0 if records and not counts['no_pdf'] and not counts['error'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
