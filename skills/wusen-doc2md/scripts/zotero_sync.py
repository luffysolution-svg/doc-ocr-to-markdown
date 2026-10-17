#!/usr/bin/env python
"""Three-way Markdown/Zotero sync through a READ-ONLY loopback API.

--apply persists a baseline and emits MCP operations; it never PATCHes/POSTs the
local API. Execute operations in order through the Zotero plugin, checking each
precondition and stopping on any failed/HTTP error. Re-run this CLI to verify
readback and commit the baseline. Exit 2 means external operations remain.
Initial unequal fields require --initial-direction local|remote. Empty local
values cannot clear remote data. authors/relations writes are unsupported.
"""
from __future__ import annotations

import argparse
import copy
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from console_output import safe_print as print
from markdown_metadata import split_frontmatter, replace_frontmatter
from zotero_export import (DEFAULT_BASE_URL, ExportError, FileLock, LocalAPI,
                           _check_destination, _data, library_identity,
                           read_owned, replace_checked, sha256, valid_key, write_owned)

URI_PATTERN = re.compile(r'zotero://select/(?P<library>library|groups/(?P<group>\d+))/items/(?P<key>[A-Z0-9]{8})\Z')
FIELD_MAPPING = {
    'title': 'title', 'publication_title': 'publicationTitle', 'volume': 'volume',
    'issue': 'issue', 'pages': 'pages', 'date': 'date', 'doi': 'DOI',
    'abstract': 'abstractNote', 'url': 'url', 'language': 'language',
}
ANN_BEGIN = '<!-- zotero-sync:annotations:begin -->'
ANN_END = '<!-- zotero-sync:annotations:end -->'


def extract_frontmatter_and_content(md_path: Path) -> tuple[dict, str]:
    return split_frontmatter(md_path.read_bytes().decode('utf-8'))


def extract_notes_section(content: str) -> str | None:
    region = _annotation_region(content)
    if region:
        content = content[:region[0]] + content[region[1]:]
    headers = {'阅读笔记', '读书笔记', '核心结论', '笔记', 'Notes'}
    captured, capturing, fence = [], False, None
    for line in content.splitlines():
        stripped = line.strip()
        marker = re.match(r'^(`{3,}|~{3,})', stripped)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            if capturing:
                captured.append(line)
            continue
        heading = re.fullmatch(r'##[ \t]+(.+?)[ \t]*#*[ \t]*', stripped) if not fence else None
        if heading:
            if capturing:
                break
            capturing = heading[1] in headers
        elif capturing:
            captured.append(line)
    return '\n'.join(captured).strip() or None


def markdown_to_zotero_html(md_text: str) -> str:
    try:
        import markdown
    except ImportError as exc:
        raise ValueError('Note synchronization requires the markdown package used for MCP-compatible rendering') from exc
    return '<div data-schema-version="9">' + markdown.markdown(md_text) + '</div>'


def _legacy_note_html(md_text: str) -> str:
    """Recognize a pending plan from the earlier simple serializer."""
    paragraphs = []
    for para in md_text.split('\n\n'):
        para = para.strip()
        if not para:
            continue
        if para.startswith(('- ', '* ')):
            items = [f'<li>{html.escape(item[2:])}</li>' for item in para.splitlines()]
            paragraphs.append(f'<ul>{"".join(items)}</ul>')
        elif para.startswith('### '):
            paragraphs.append(f'<h3>{html.escape(para[4:])}</h3>')
        elif para.startswith('## '):
            paragraphs.append(f'<h2>{html.escape(para[3:])}</h2>')
        elif para.startswith('> '):
            paragraphs.append(f'<blockquote>{html.escape(para[2:])}</blockquote>')
        else:
            paragraphs.append(f'<p>{html.escape(para)}</p>')
    return '<div data-schema-version="9">' + ''.join(paragraphs) + '</div>'


class ZoteroSyncClient(LocalAPI):
    """The official local API is treated as read-only, even under --apply."""
    def get_item(self, item_key):
        with self.get(f'/items/{valid_key(item_key)}') as response:
            result = json.load(response)
        _data(result)
        if result.get('key', result['data'].get('key')) != item_key:
            raise ExportError('API returned a different item key')
        return result

    def get_children(self, item_key):
        return list(self.items(f'/items/{valid_key(item_key)}/children'))

    def get_annotations(self):
        # Zotero 10 /attachment/children excludes annotations even when they exist.
        # Use the official library annotation listing with full pagination instead.
        return list(self.items('/items', {'itemType': 'annotation'}))

    def for_document(self, metadata):
        match = URI_PATTERN.fullmatch(str(metadata.get('zotero_item_uri', '')))
        if not match:
            raise ValueError('Missing valid zotero_item_uri')
        route = re.search(r'/api/(users|groups)/(\d+)$', self.base_url)
        if match['group']:
            if route[1] != 'groups' or int(route[2]) != int(match['group']):
                raise ValueError('Group document requires explicit matching --base-url /api/groups/ID')
        elif route[1] != 'users':
            raise ValueError('Personal document cannot use group API')
        return match['key']


def _value(field, value):
    if field == 'tags':
        if not isinstance(value, list) or any(not isinstance(t, str) or not t for t in value):
            raise ValueError('tags must be a list of nonempty strings')
        return sorted(set(value))
    if value is None:
        return ''
    if not isinstance(value, (str, int, float)):
        raise ValueError(f'{field} must be scalar text')
    value = str(value)
    if field != 'abstract':
        value = value.strip()
    if field == 'doi':
        value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value, flags=re.I).lower()
    return value


def _remote_fields(item):
    data = _data(item)
    result = {field: _value(field, data.get(remote)) for field, remote in FIELD_MAPPING.items()}
    result['tags'] = sorted({t['tag'] for t in data.get('tags', []) if t.get('tag')})
    return result


def compute_metadata_diff(frontmatter: dict, zotero_item: dict) -> dict:
    remote = _remote_fields(zotero_item)
    diff = {name: {} for name in ('identical', 'different', 'local_only', 'zotero_only', 'unsupported')}
    for field, other in remote.items():
        if field not in frontmatter:
            if other:
                diff['zotero_only'][field] = other
            continue
        local = _value(field, frontmatter[field])
        if local == other:
            diff['identical'][field] = local
        elif not other:
            diff['local_only'][field] = local
        else:
            diff['different'][field] = {'local': local, 'zotero': other}
    if 'authors' in frontmatter:
        authors = [c.get('name') or ' '.join(filter(None, [c.get('firstName'), c.get('lastName')]))
                   for c in _data(zotero_item).get('creators', []) if c.get('creatorType') == 'author']
        if frontmatter['authors'] == authors:
            diff['identical']['authors'] = authors
        else:
            diff['unsupported']['authors'] = 'Display names cannot losslessly map creators; MCP update_item has no creators argument'
    for field in ('relations', 'has_supplement', 'supplement_to'):
        if field in frontmatter:
            diff['unsupported'][field] = 'No lossless relation mapping through the installed MCP write interface'
    return diff


def _note_content_hash(note_html):
    """Ignore renderer wrappers/inline tags but verify the complete visible text."""
    from html.parser import HTMLParser
    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
        def handle_starttag(self, tag, attrs):
            if tag in ('p', 'li', 'h1', 'h2', 'h3', 'blockquote', 'br', 'div'):
                self.parts.append(' ')
        def handle_endtag(self, tag):
            self.handle_starttag(tag, [])
    parser = Text()
    parser.feed(note_html)
    return sha256(re.sub(r'\s+', ' ', ''.join(parser.parts)).strip().encode())


def _operation(kind, library, key, version, before, after, **extra):
    payload = {'kind': kind, 'library': library, 'key': key, 'version': version,
               'before': before, 'after': after, **extra}
    payload['id'] = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())[:24]
    payload['status'] = 'pending_mcp'
    payload['preconditions'] = 'Read current item; require matching library, key, version and before values. Stop on error. Re-read after write.'
    return payload


def _annotation_region(text):
    if text.count(ANN_BEGIN) != text.count(ANN_END) or text.count(ANN_BEGIN) > 1:
        raise ValueError('Ambiguous managed annotation boundaries')
    if ANN_BEGIN not in text:
        return None
    start, finish = text.index(ANN_BEGIN), text.index(ANN_END) + len(ANN_END)
    if finish <= start:
        raise ValueError('Invalid managed annotation boundary order')
    return start, finish, text[start:finish]


def _canonical_hash(text):
    """Hash rendered prose independent of the host's newline convention."""
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    return sha256(normalized.encode('utf-8'))


def _render_annotations(annotations):
    lines = [ANN_BEGIN, '## Zotero 批注与高亮（受管区域）', '']
    for ann in annotations:
        lines += [f'<!-- annotation:{ann["attachment_key"]}:{ann["key"]} -->',
                  f'### {ann["attachment_key"]}/{ann["key"]} · Page {ann["page"]}',
                  '```text', ann['text'].replace('```', '｀｀｀'), ann['comment'].replace('```', '｀｀｀'), '```', '']
    return '\n'.join(lines + [ANN_END])


def sync_document(md_path: Path, client: ZoteroSyncClient, apply=False,
                  pull_annotations=False, initial_direction=None, state_path=None) -> dict:
    md_path = Path(md_path).absolute()
    state_path = Path(state_path) if state_path else md_path.with_name('.' + md_path.name + '.zotero-sync.json')
    if state_path.absolute() == md_path:
        raise ValueError('State path collides with input')
    owner = f'zotero-sync:{md_path}'
    with FileLock(state_path.with_name(state_path.name + '.lock')):
        return _sync_document(md_path, client, apply, pull_annotations, initial_direction, state_path, owner)


def _sync_document(md_path, client, apply, pull_annotations, initial_direction, state_path, owner):
    original = md_path.read_bytes()
    _check_destination(md_path, original)
    text = original.decode('utf-8')
    meta, content = split_frontmatter(text)
    key = client.for_document(meta)
    state = read_owned(state_path, owner) or {'base': {}, 'pending': {}, 'note': {}}
    state = copy.deepcopy(state)
    if (state.get('document_before_sha256') != state.get('document_after_sha256')
            and sha256(original) == state.get('document_before_sha256')
            and 'recovery_previous' in state):
        # State was published but the atomic Markdown publication failed.
        # Retry from the old baseline rather than treating old text as a new edit.
        state = state['recovery_previous']
    state.pop('recovery_previous', None)
    prior_state = copy.deepcopy(state)
    item = client.get_item(key)
    library = library_identity(client, item)
    explicit = meta.get('zotero_library_id')
    if explicit is not None and str(explicit) != str(library['id']):
        raise ValueError('Frontmatter library ID mismatch')
    if meta.get('zotero_library_type', library['type']) != library['type']:
        raise ValueError('Frontmatter library type mismatch')
    identity = {'library': library, 'key': key}
    if state.get('identity', identity) != identity:
        raise ValueError('Persistent sync state belongs to a different library/item')
    state['identity'] = identity
    version = item.get('version', _data(item).get('version'))
    if not isinstance(version, int):
        raise ValueError('Missing API item version')
    remote = _remote_fields(item)
    diff = compute_metadata_diff(meta, item)
    report = {'file': str(md_path), 'item_key': key, 'library': library, 'version': version,
              'apply_mode': apply, 'status': 'ok', 'metadata_diff': diff, 'fields': {},
              'operations': [], 'conflicts': [], 'unsupported': diff['unsupported'],
              'metadata_updated': False, 'notes_synced_to_zotero': False,
              'state_path': str(state_path), 'transport': 'read_only_local_api; writes_via_MCP_only'}
    pulls, pushes, before = {}, {}, {}
    for field, remote_value in remote.items():
        present = field in meta
        local = _value(field, meta[field]) if present else None
        base = state['base'].get(field)
        pending = state['pending'].get(field)
        status = 'identical'
        if pending:
            if remote_value == pending['after']:
                base = pending['after']
                state['base'][field] = base
                del state['pending'][field]
                status = 'verified_remote_write'
            elif remote_value != pending['before']:
                report['conflicts'].append({'field': field, 'reason': 'remote changed while MCP operation pending'})
                report['fields'][field] = {'status': 'conflict'}
                continue
        if not present:
            # A missing local field means pull, never a request to delete remote.
            pulls[field] = remote_value
            status = 'pull'
        elif local == remote_value:
            state['base'][field] = remote_value
        elif field not in state['base']:
            if initial_direction == 'remote':
                pulls[field], status = remote_value, 'pull'
            elif initial_direction == 'local':
                pushes[field], before[field], status = local, remote_value, 'push'
            else:
                status = 'initial_direction_required'
                report['conflicts'].append({'field': field, 'reason': status})
        elif local == base:
            pulls[field], status = remote_value, 'pull'
        elif remote_value == base:
            pushes[field], before[field], status = local, remote_value, 'push'
        else:
            status = 'conflict'
            report['conflicts'].append({'field': field, 'base': base, 'local': local, 'remote': remote_value})
        if field in pushes and not pushes[field] and remote_value:
            del pushes[field]
            status = 'blocked_empty_clear'
            report['conflicts'].append({'field': field, 'reason': status})
        report['fields'][field] = {'status': status, 'local': local, 'remote': remote_value, 'base': base}
    if pushes:
        fields = {FIELD_MAPPING[f]: value for f, value in pushes.items() if f in FIELD_MAPPING}
        parameters = {'key': key, 'library': 'user' if library['type'] == 'user' else f'g{library["id"]}', 'fields': fields}
        if 'tags' in pushes:
            parameters['addTags'] = sorted(set(pushes['tags']) - set(remote['tags']))
            parameters['removeTags'] = sorted(set(remote['tags']) - set(pushes['tags']))
            automatic = {t['tag'] for t in _data(item).get('tags', []) if t.get('type') == 1}
            if automatic & set(parameters['removeTags']):
                report['conflicts'].append({'field': 'tags', 'reason': 'Refusing to remove automatic Zotero tags'})
        report['operations'].append(_operation('metadata', library, key, version, before, pushes,
                                               tool='update_item', parameters=parameters))
        for field, value in pushes.items():
            state['pending'][field] = {'before': remote[field], 'after': value}
    # Read all child pages before creating any local output or a write plan.
    children = client.get_children(key)
    attachments = [c for c in children if _data(c).get('itemType') == 'attachment' and not _data(c).get('deleted')]
    notes = [c for c in children if _data(c).get('itemType') == 'note' and not _data(c).get('deleted')]
    report['attachments'] = []
    annotations = []
    annotation_items = client.get_annotations() if pull_annotations else []
    for ann in annotation_items:
        library_identity(client, ann)
        if _data(ann).get('itemType') != 'annotation':
            raise ValueError('Annotation endpoint returned an unsupported snapshot')
    for att in attachments:
        data = _data(att)
        att_key = valid_key(att.get('key') or data.get('key'))
        role = 'supplemental_candidate' if re.search(r'supplement|supporting|appendix|\bSI\b', data.get('title', ''), re.I) else 'unclassified'
        audit = {'key': att_key, 'title': data.get('title'), 'role': role, 'link_mode': data.get('linkMode'),
                 'status': 'not_pdf', 'readable': None}
        if data.get('contentType', '').lower() == 'application/pdf':
            try:
                raw = client.pdf(att_key)
                audit.update(status='ready', readable=True, sha256=sha256(raw), bytes=len(raw))
            except Exception as exc:
                audit.update(status='error', readable=False, error=str(exc))
        report['attachments'].append(audit)
        if pull_annotations:
            for ann in annotation_items:
                adata = _data(ann)
                if adata.get('parentItem') != att_key or adata.get('deleted'):
                    continue
                annotations.append({'attachment_key': att_key, 'key': valid_key(ann.get('key') or adata.get('key')),
                                    'text': str(adata.get('annotationText', '')), 'comment': str(adata.get('annotationComment', '')),
                                    'color': adata.get('annotationColor'), 'page': str(adata.get('annotationPageLabel', ''))})
    report['attachments_count'] = len(attachments)
    report['notes_in_zotero_count'] = len(notes)
    # Stable source identity survives content edits. Never touch unmarked user notes.
    marker = 'KB-SYNC:' + sha256(json.dumps(identity, sort_keys=True).encode() + str(md_path).encode())[:24]
    note_state = state['note']
    marker = note_state.get('marker', marker)
    managed = [n for n in notes if marker in _data(n).get('note', '')]
    if len(managed) > 1:
        report['conflicts'].append({'field': 'note', 'reason': 'Multiple managed notes'})
    local_notes = extract_notes_section(content)
    report['local_notes_detected'] = bool(local_notes)
    expected_note = markdown_to_zotero_html(f'{marker}\n\n{local_notes}') if local_notes else None
    current = managed[0] if len(managed) == 1 else None
    # Migrate only an unchanged local source matching the exact old pending hash.
    # Never adopt a remote note merely because it contains our marker.
    if (expected_note and note_state.get('pending_hash') and not note_state.get('pending_content_hash')
            and sha256(_legacy_note_html(f'{marker}\n\n{local_notes}').encode()) == note_state['pending_hash']):
        note_state.update(pending_content_hash=_note_content_hash(expected_note),
                          pending_local_hash=sha256(local_notes.encode()))
    if current:
        note_key = valid_key(current.get('key') or _data(current).get('key'))
        remote_html = _data(current).get('note', '')
        remote_hash = sha256(remote_html.encode())
        if note_state.get('key', note_key) != note_key:
            report['conflicts'].append({'field': 'note', 'reason': 'Managed note key changed'})
        elif (remote_hash == note_state.get('pending_hash') or
              _note_content_hash(remote_html) == note_state.get('pending_content_hash')):
            note_state.update(key=note_key, hash=remote_hash, marker=marker,
                              local_hash=note_state.get('pending_local_hash'))
            for name in ('pending_hash', 'pending_content_hash', 'pending_local_hash'):
                note_state.pop(name, None)
        elif note_state.get('hash') != remote_hash:
            report['conflicts'].append({'field': 'note', 'reason': 'Managed note edited remotely or missing baseline'})
        if expected_note and (sha256(expected_note.encode()) == remote_hash or
                              sha256(local_notes.encode()) == note_state.get('local_hash')):
            report['notes_synced_to_zotero'] = not any(c['field'] == 'note' for c in report['conflicts'])
        elif expected_note:
            report['operations'].append(_operation('note_update', library, note_key,
                current.get('version', _data(current).get('version')), {'note_sha256': remote_hash},
                {'note_sha256': sha256(expected_note.encode())}, tool='update_item',
                requires_capability_verification='fields.note must be tested on a dedicated note; no assumed overwrite support',
                parameters={'key': note_key, 'library': 'user' if library['type'] == 'user' else f'g{library["id"]}', 'fields': {'note': expected_note}}))
            note_state.update(pending_hash=sha256(expected_note.encode()),
                              pending_content_hash=_note_content_hash(expected_note),
                              pending_local_hash=sha256(local_notes.encode()))
    elif note_state.get('key'):
        report['conflicts'].append({'field': 'note', 'reason': 'Previously managed remote note was deleted'})
    elif expected_note:
        report['operations'].append(_operation('note_create', library, key, version, {'marker_absent': marker},
            {'note_sha256': sha256(expected_note.encode())}, tool='add_note',
            parameters={'parentKey': key, 'library': 'user' if library['type'] == 'user' else f'g{library["id"]}', 'markdown': f'{marker}\n\n{local_notes}'},
            expected_html=expected_note, marker=marker))
        note_state.update(marker=marker, pending_hash=sha256(expected_note.encode()),
                          pending_content_hash=_note_content_hash(expected_note),
                          pending_local_hash=sha256(local_notes.encode()))
    updated = text
    previous_annotations = copy.deepcopy(state.get('annotations', {}))
    if pull_annotations:
        annotations.sort(key=lambda a: (a['attachment_key'], a['key']))
        region = _annotation_region(text)
        if region and state.get('annotation_hash') != _canonical_hash(region[2]):
            report['conflicts'].append({'field': 'annotations', 'reason': 'Managed annotation region edited or unowned'})
        elif not region and state.get('annotation_hash'):
            report['conflicts'].append({'field': 'annotations', 'reason': 'Managed annotation region removed locally'})
        else:
            rendered = _render_annotations(annotations)
            updated = text[:region[0]] + rendered + text[region[1]:] if region else text + '\n\n' + rendered + '\n'
            state['annotation_hash'] = _canonical_hash(rendered)
            state['annotations'] = {f'{a["attachment_key"]}:{a["key"]}': a for a in annotations}
    if pull_annotations:
        returned = {f'{a["attachment_key"]}:{a["key"]}' for a in annotations}
        for identity in set(previous_annotations) - returned:
            old = previous_annotations[identity]
            try:
                existing = client.get_item(old['key'])
            except ExportError as exc:
                if 'HTTP 404 ' not in str(exc):
                    raise
            else:
                data = _data(existing)
                if not data.get('deleted'):
                    report['conflicts'].append({'field': 'annotations',
                        'reason': f'Incomplete annotation listing: known annotation {old["key"]} still exists'})
    report['annotations_pulled'] = len(annotations)
    report['annotation_snapshot'] = annotations
    if report['conflicts'] or report['unsupported'] or any(a['status'] == 'error' for a in report['attachments']):
        report['status'] = 'blocked'
        for operation in report['operations']:
            operation['status'] = 'blocked'
        # No partial advancement of state or downstream local writes after failure.
        return report
    for index, operation in enumerate(report['operations']):
        operation['depends_on'] = [op['id'] for op in report['operations'][:index]]
        if index:
            operation['dependency_version_policy'] = ('If an earlier operation changed this same item, use its verified readback version; '
                'recheck all before values and reject any unrelated change. Never ignore an unexpected version.')
    if pulls:
        for field, value in pulls.items():
            if value:
                meta[field] = value
            else:
                meta.pop(field, None)
        state['base'].update(pulls)
        updated = replace_frontmatter(updated, meta)
    report['status'] = 'pending_mcp' if report['operations'] else 'ok'
    report['local_updates'] = sorted(pulls)
    if apply:
        # Save baseline/pending BEFORE local content; interrupted writes fail closed.
        state['document_before_sha256'] = sha256(original)
        state['document_after_sha256'] = sha256(updated.encode())
        write_owned(state_path, state, owner)
        replace_checked(md_path, updated.encode(), original)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL)
    parser.add_argument('--apply', action='store_true', help='Persist local sync and emit MCP plan; no HTTP writes')
    parser.add_argument('--pull-annotations', action='store_true')
    parser.add_argument('--initial-direction', choices=('local', 'remote'))
    parser.add_argument('--report', help='Owned JSON report/operation plan; required for --apply')
    args = parser.parse_args(argv)
    try:
        if args.apply and not args.report:
            raise ValueError('--apply requires --report for the auditable MCP operation plan')
        target = Path(args.path).absolute()
        if not target.exists():
            raise ValueError('Input path does not exist')
        files = [target] if target.is_file() else sorted(target.rglob('*.md'))
        files = [f for f in files if f.name != '00-目录.md']
        if not files:
            raise ValueError('No Markdown input documents')
        owner = f'zotero-sync-report:{target}'
        report_path = Path(args.report).absolute() if args.report else None
        if report_path:
            protected = {f.absolute() for f in files}
            protected.update(f.with_name('.' + f.name + '.zotero-sync.json') for f in files)
            if report_path in protected or (target.is_dir() and report_path.is_relative_to(target)):
                raise ValueError('Report path collides with input/state directory')
            read_owned(report_path, owner)  # Fail before any document changes.
        client = ZoteroSyncClient(args.base_url)
        reports = []
        # This lock covers report preflight and the entire batch.
        lock = FileLock(report_path.with_name(report_path.name + '.lock')) if report_path else None
        def run():
            if report_path:
                read_owned(report_path, owner)
            for path in files:
                try:
                    report = sync_document(path, client, args.apply, args.pull_annotations, args.initial_direction)
                except Exception as exc:
                    report = {'file': str(path), 'status': 'error', 'error': str(exc), 'operations': []}
                reports.append(report)
                print(f'{path.name}: {report["status"]}')
            if report_path:
                write_owned(report_path, {'schema': 1, 'reports': reports,
                    'execution': 'MCP only; check preconditions before every operation; stop on failed response or HTTP error; rerun CLI to verify'}, owner)
        if lock:
            with lock:
                run()
        else:
            run()
        if any(r['status'] in ('error', 'blocked') for r in reports):
            return 1
        return 2 if any(r['status'] == 'pending_mcp' for r in reports) else 0
    except Exception as exc:
        print(f'Sync failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
