"""Explicit book repairs and portable, source-exact retrieval indexes.

No builder import: publication/ownership checks belong to build_support.Publisher.
All document line ranges are one-based and inclusive; PDF pages are global.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import json
import math
from pathlib import Path, PureWindowsPath

from markdown_it import MarkdownIt

from build_support import contained, relative_name
from normalize_math import scan_math


_TYPES = {'text', 'body_text', 'page_footnote', 'equation'}
_INDEX_FILES = ('agent/chunks.jsonl', 'agent/page-map.json', 'agent/manifest.json')


def _positive(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f'{label} must be a positive integer: {value!r}')
    return value


def _box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or not (0 <= value[0] < value[2] <= 1000
                    and 0 <= value[1] < value[3] <= 1000)):
        raise ValueError(f'bbox out of bounds (normalized 0..1000): {value!r}')
    return tuple(value)


def apply_repairs(blocks, repairs, pdf_path=None):
    """Apply guarded operations to original blocks, returning copies and full audit.

    Text: {page,bbox,field:'text',old,new,reason[,set_type]}.
    Type/skip: {page,bbox,action:'set_type'|'skip',old,reason[,set_type]}.
    Add image: {page,bbox,action:'add_image',reason,after:{bbox,text}}; requires PDF.
    Update image/chart: {page,bbox,action:'update_image',old,reason[,new_bbox]}.
    Image actions accept image_caption/image_footnote as lists of strings;
    updates write them to the original type's image_* or chart_* fields.
    Update old has exactly type,img_path,bbox and that type's caption/footnote;
    missing original img_path defaults to '', captions/footnotes to [].
    Updates must change geometry or captions. Geometry changes require a source
    PDF and set _recrop_required=True. Explicit images retain boundaries.
    A block may be edited once. All anchors/old values refer to ORIGINAL input;
    neither chaining edits nor silently accepting stale records is permitted.
    """
    if not isinstance(blocks, list) or not isinstance(repairs, list):
        raise ValueError('blocks and repairs must be JSON lists')
    originals = deepcopy(blocks)
    result = deepcopy(blocks)
    locations = {}
    for index, block in enumerate(originals):
        if not isinstance(block, dict):
            raise ValueError('each block must be an object')
        page = _positive(block.get('_page'), 'block _page')
        # Some OCR metadata blocks have no geometry; they cannot be repaired.
        if block.get('bbox') is not None:
            locations.setdefault((page, _box(block['bbox'])), []).append(index)
    pdf_pages = None
    if pdf_path is not None:
        import fitz
        with fitz.open(pdf_path) as pdf:
            pdf_pages = len(pdf)
    source_pages = {b['_page'] for b in originals}
    edits, insertions, images, audit = set(), {}, set(), []
    for record in repairs:
        if not isinstance(record, dict):
            raise ValueError('each repair must be an object')
        page = _positive(record.get('page'), 'repair page')
        box = _box(record.get('bbox'))
        if (pdf_pages is not None and page > pdf_pages) or page not in source_pages:
            raise ValueError(f'repair page out of bounds: {page}')
        if not isinstance(record.get('reason'), str) or not record['reason'].strip():
            raise ValueError('repair requires a nonempty reason')
        if 'confidence' in record and record['confidence'] not in ('high', 'manual_review'):
            raise ValueError('repair confidence must be high or manual_review')
        action = record.get('action', 'replace')
        if not isinstance(action, str) or action not in {'replace', 'skip', 'set_type', 'add_image', 'update_image'}:
            raise ValueError(f'unsupported repair action: {action!r}')
        image_fields = ('image_caption', 'image_footnote')
        if action in ('add_image', 'update_image'):
            allowed = {'page', 'bbox', 'action', 'reason', 'confidence', *image_fields}
            allowed.update(('after',) if action == 'add_image' else ('old', 'new_bbox'))
            if record.keys() - allowed:
                raise ValueError('image repair contains inapplicable fields')
            for field in image_fields:
                if field in record and (not isinstance(record[field], list)
                                        or any(not isinstance(v, str) for v in record[field])):
                    raise ValueError(f'{field} must be a list of strings')
        elif any(key in record for key in (*image_fields, 'new_bbox', 'after')):
            raise ValueError('text/type/skip repair conflicts with image repair fields')
        if action == 'add_image':
            if pdf_path is None:
                raise ValueError('add_image requires source PDF')
            anchor = record.get('after')
            if not isinstance(anchor, dict) or not isinstance(anchor.get('text'), str):
                raise ValueError('add_image requires after:{bbox,text}')
            candidates = locations.get((page, _box(anchor.get('bbox'))), [])
            candidates = [i for i in candidates if originals[i].get('text', '') == anchor['text']]
            if len(candidates) != 1:
                raise ValueError('image anchor must uniquely match an original block')
            key = (page, box)
            if key in images or any(originals[i].get('type') in ('image', 'chart')
                                    for i in locations.get(key, [])):
                raise ValueError('duplicate/conflicting image repair')
            images.add(key)
            index = candidates[0]
            original = originals[index]
            image = {key: deepcopy(original[key]) for key in ('_page', '_ocr_dir', 'page_idx')
                     if key in original}
            image.update(type='image', bbox=list(box), _added_image=True, _figure_boundary=True)
            image.update({field: deepcopy(record[field]) for field in image_fields if field in record})
            insertions.setdefault(index, []).append(image)
        elif action == 'update_image':
            candidates = locations.get((page, box), [])
            if len(candidates) != 1:
                raise ValueError('repair must uniquely match an original page/bbox')
            index = candidates[0]
            original = originals[index]
            if original.get('type') not in ('image', 'chart'):
                raise ValueError('update_image requires an original image/chart')
            caption_fields = (f'{original["type"]}_caption', f'{original["type"]}_footnote')
            snapshot = {field: deepcopy(original.get(field, default)) for field, default in
                        (('type', None), ('img_path', ''), ('bbox', None),
                         (caption_fields[0], []), (caption_fields[1], []))}
            if not isinstance(record.get('old'), dict) or record['old'] != snapshot:
                raise ValueError('stale repair: old image snapshot does not match original')
            if index in edits:
                raise ValueError('duplicate/conflicting repair for original block')
            changes = {target: deepcopy(record[field]) for field, target in
                       zip(image_fields, caption_fields) if field in record}
            if 'new_bbox' in record:
                changes['bbox'] = list(_box(record['new_bbox']))
            if not any(value != snapshot[field] for field, value in changes.items()):
                raise ValueError('update_image requires an actual change')
            if 'bbox' in changes and _box(changes['bbox']) != box:
                if pdf_path is None:
                    raise ValueError('image bbox change requires source PDF')
                changes['_recrop_required'] = True
            edits.add(index)
            result[index].update(changes, _figure_boundary=True)
        else:
            candidates = locations.get((page, box), [])
            if len(candidates) != 1:
                raise ValueError('repair must uniquely match an original page/bbox')
            index = candidates[0]
            original = originals[index]
            if not isinstance(record.get('old'), str) or original.get('text', '') != record['old']:
                raise ValueError('stale repair: old text does not match original')
            if index in edits:
                raise ValueError('duplicate/conflicting repair for original block')
            edits.add(index)
            if 'set_type' in record and (not isinstance(record['set_type'], str)
                                         or record['set_type'] not in _TYPES):
                raise ValueError('unsupported set_type')
            if action == 'replace':
                if record.get('field') != 'text' or not isinstance(record.get('new'), str):
                    raise ValueError('text repair requires field=text and string new')
                result[index]['text'] = record['new']
            elif any(key in record for key in ('field', 'new')):
                raise ValueError('skip/set_type conflicts with text repair fields')
            if action == 'skip':
                if 'set_type' in record:
                    raise ValueError('skip conflicts with set_type')
                result[index] = None
            elif action == 'set_type' and 'set_type' not in record:
                raise ValueError('set_type action requires set_type')
            if 'set_type' in record:
                result[index]['type'] = record['set_type']
        audit.append({**deepcopy(record), 'applied': True})
    if any(result[index] is None for index in insertions):
        raise ValueError('skip conflicts with an add_image anchor')
    output = []
    for index, block in enumerate(result):
        if block is not None:
            output.append(block)
        output.extend(insertions.get(index, []))
    return output, audit


def _layout(text):
    """Return indivisible line intervals and real headings, protecting math."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans, issues = scan_math(text)
    protected = [(bisect_right(offsets, span.begin) - 1,
                  bisect_right(offsets, span.stop - 1)) for span in spans if span.display]
    # Preserve an unfinished formula as one block; validation reports the error.
    protected.extend((bisect_right(offsets, issue['offset']) - 1, len(lines)) for issue in issues)
    tokens = MarkdownIt('commonmark', {'html': True}).enable('table').parse(text)
    intervals = [tuple(token.map) for token in tokens if token.map and token.level == 0]
    intervals.extend(protected)
    # Merge overlapping ranges, but retain boundaries between adjacent blocks.
    merged = []
    for start, end in sorted(intervals):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    cuts = sorted({0, len(lines)} | {line for interval in merged for line in interval})
    headings = []
    stack = []
    for index, token in enumerate(tokens):
        if token.type != 'heading_open' or token.level != 0 or not token.map:
            continue
        line = token.map[0]
        if any(start <= line < end for start, end in protected):
            continue
        level = int(token.tag[1:])
        label = tokens[index + 1].content
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, label))
        headings.append((line, ' > '.join(value for _, value in stack)))
    return lines, cuts, headings


def _section(headings, start, title):
    return next((section for line, section in reversed(headings) if line <= start), title)


def _validate_pages(entries, line_count):
    if not isinstance(entries, list):
        raise ValueError('page map must be a list')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('page map entry must be an object')
        page = _positive(entry.get('pdf_page'), 'pdf_page')
        if page in seen:
            raise ValueError(f'duplicate PDF page mapping: {page}')
        seen.add(page)
        if not isinstance(entry.get('status'), str) or not entry['status']:
            raise ValueError('page mapping requires status')
        start, end = entry.get('start_line'), entry.get('end_line')
        if start is None and end is None:
            continue
        _positive(start, 'start_line')
        _positive(end, 'end_line')
        if not start <= end <= line_count:
            raise ValueError(f'page line range out of bounds: {start}-{end}')


def _pages_for(entries, start, end):
    return sorted(entry['pdf_page'] for entry in entries
                  if entry.get('start_line') is not None
                  and entry['start_line'] <= end and entry['end_line'] >= start)


def make_chunks(text, file, title, pages, max_chars=2400):
    """Partition source exactly at Markdown boundaries, never splitting a formula.

    Oversize single blocks are kept whole. Sections never share a chunk, and
    whitespace is retained, so concatenating text fields reconstructs the source.
    """
    relative_name(file)
    _positive(max_chars, 'max_chars')
    if not isinstance(text, str) or not isinstance(title, str):
        raise ValueError('text and title must be strings')
    lines, cuts, headings = _layout(text)
    _validate_pages(pages, len(lines))
    if not lines:
        return []
    heading_lines = {line for line, _ in headings}
    chunks = []
    begin = end = 0
    size = 0

    def emit():
        start_line, end_line = begin + 1, end
        chunks.append({'id': f'{file}:L{start_line}-L{end_line}', 'file': file,
                       'title': title, 'section': _section(headings, begin, title),
                       'start_line': start_line, 'end_line': end_line,
                       'pages': _pages_for(pages, start_line, end_line),
                       'text': ''.join(lines[begin:end])})

    for start, stop in zip(cuts, cuts[1:]):
        block_size = sum(len(line) for line in lines[start:stop])
        if end > begin and (size + block_size > max_chars or start in heading_lines):
            emit()
            begin, size = start, 0
        end = stop
        size += block_size
    if end > begin:
        emit()
    return chunks


def _source_reference(value):
    if value is None:
        return None
    value = str(value)
    if (not value or '\\' in value or ':' in value or value.startswith('/')
            or PureWindowsPath(value).is_absolute() or any(ord(c) < 32 for c in value)):
        raise ValueError('source_pdf must be a portable reference relative to output root')
    return value


def _chapters(chapters):
    if not isinstance(chapters, list):
        raise ValueError('chapters must be a list')
    ids, files, result = set(), set(), []
    for chapter in chapters:
        identity = relative_name(chapter['id'], component=True)
        file = relative_name(chapter['file'])
        title = chapter['title']
        if (identity in ids or file.casefold() in files or not file.endswith('.md')
                or file.split('/')[0].casefold() == 'agent'
                or not isinstance(title, str) or not title.strip()):
            raise ValueError('duplicate/invalid chapter id, file or title')
        ids.add(identity)
        files.add(file.casefold())
        result.append({'id': identity, 'file': file, 'title': title})
    return result


def write_agent_index(stage, chapters, page_maps, source_pdf=None):
    """Write three fresh staging files; Publisher owns destination collisions.

    source_pdf is supplied relative to the FINAL output root, not to stage.
    It may point outside that root (../source.pdf), and is never opened here.
    """
    stage = Path(stage)
    chapters = _chapters(chapters)
    source_pdf = _source_reference(source_pdf)
    if not isinstance(page_maps, dict) or set(page_maps) != {c['id'] for c in chapters}:
        raise ValueError('page_maps must contain exactly the chapter ids')
    chunks = []
    for chapter in chapters:
        with contained(stage, chapter['file']).open(encoding='utf-8', newline='') as stream:
            text = stream.read()
        chunks.extend(make_chunks(text, chapter['file'], chapter['title'], page_maps[chapter['id']]))
    manifest = {'version': 1, 'chapters': chapters, 'chunks': _INDEX_FILES[0],
                'page_map': _INDEX_FILES[1], 'source_pdf': source_pdf,
                'source_pdf_base': 'output_root'}
    paths = [contained(stage, name) for name in _INDEX_FILES]
    if any(path.exists() for path in paths):
        raise ValueError('agent index staging files already exist; refusing overwrite')
    payloads = [''.join(json.dumps(chunk, ensure_ascii=False) + '\n' for chunk in chunks),
                json.dumps(page_maps, ensure_ascii=False, indent=2) + '\n',
                json.dumps(manifest, ensure_ascii=False, indent=2) + '\n']
    for path, payload in zip(paths, payloads):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding='utf-8', newline='')


def validate_agent_index(root):
    """Return diagnostics without writing; legacy outputs without indexes pass."""
    root = Path(root)
    errors = []
    try:
        manifest_path = contained(root, _INDEX_FILES[2])
        if not manifest_path.exists():
            return []
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('version') != 1 or manifest.get('source_pdf_base') != 'output_root':
            raise ValueError('unsupported agent manifest version/source base')
        _source_reference(manifest.get('source_pdf'))
        chapters = _chapters(manifest['chapters'])
        page_maps = json.loads(contained(root, manifest['page_map']).read_text(encoding='utf-8'))
        chunk_path = contained(root, manifest['chunks'])
        chunks = [json.loads(line) for line in chunk_path.read_text(encoding='utf-8').splitlines() if line.strip()]
        if not isinstance(page_maps, dict) or set(page_maps) != {c['id'] for c in chapters}:
            raise ValueError('page-map chapter coverage mismatch')
        by_file = {c['file']: [] for c in chapters}
        identities = set()
        for chunk in chunks:
            contained(root, chunk['file'])
            if chunk['file'] not in by_file or chunk['id'] in identities:
                raise ValueError('unknown chapter or duplicate chunk id')
            identities.add(chunk['id'])
            by_file[chunk['file']].append(chunk)
        for chapter in chapters:
            file = chapter['file']
            with contained(root, file).open(encoding='utf-8', newline='') as stream:
                text = stream.read()
            lines, cuts, headings = _layout(text)
            entries = page_maps[chapter['id']]
            _validate_pages(entries, len(lines))
            _, issues = scan_math(text)
            errors.extend(f'{file}: incomplete formula: {issue["error"]}' for issue in issues)
            cursor = 1
            document_chunks = sorted(by_file[file], key=lambda c: c['start_line'])
            for chunk in document_chunks:
                start = _positive(chunk['start_line'], 'chunk start_line')
                end = _positive(chunk['end_line'], 'chunk end_line')
                if start != cursor or not start <= end <= len(lines):
                    errors.append(f'{file}: chunk coverage gap/overlap at line {cursor}')
                if start - 1 not in cuts or end not in cuts:
                    errors.append(f'{file}: chunk splits a Markdown block or formula')
                if any(start - 1 < line < end for line, _ in headings):
                    errors.append(f'{file}: chunk crosses section heading')
                expected = ''.join(lines[start - 1:end])
                if chunk['text'] != expected:
                    errors.append(f'{file}: chunk text differs from source lines {start}-{end}')
                if chunk['pages'] != _pages_for(entries, start, end):
                    errors.append(f'{file}: chunk PDF page mapping mismatch')
                if chunk['title'] != chapter['title'] or chunk['section'] != _section(headings, start - 1, chapter['title']):
                    errors.append(f'{file}: chunk title/section mismatch')
                cursor = end + 1
            if cursor != len(lines) + 1 or ''.join(c['text'] for c in document_chunks) != text:
                errors.append(f'{file}: chunks do not cover the complete document')
            mapped = set()
            for entry in entries:
                if entry.get('start_line') is not None:
                    mapped.update(range(entry['start_line'], entry['end_line'] + 1))
            # The generated chapter H1 precedes the first source page.
            exempt = set()
            for token in MarkdownIt('commonmark').parse(text):
                if token.type == 'heading_open' and token.tag == 'h1' and token.map:
                    exempt.update(range(token.map[0] + 1, token.map[1] + 1))
                    break
            missing = [i for i, line in enumerate(lines, 1) if line.strip() and i not in mapped and i not in exempt]
            if missing:
                errors.append(f'{file}: page-map does not cover content lines {missing[:10]}')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append(f'agent index: {exc}')
    return errors
