#!/usr/bin/env python
"""Build one research PDF using the existing public Markdown builder and validators.

--raw accepts one item directory, a raw root containing item directories, or an
OCR workspace with raw/. A multi-item root requires --id (or a unique exported
item identity). --out is this document's delivery directory (原文.md + navigation
+ images). Work state stays outside it. This command never submits/uploads OCR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from build_support import Publisher, contained, relative_name, read_items
from research_metadata import (collect_metadata, export_identity, inspect_pdf,
                               load_export, sha256_file)

SCRIPTS = Path(__file__).resolve().parent


def write_json(path: Path, data) -> None:
    """Atomic state update; unchanged input records keep their timestamps."""
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if path.is_file() and path.read_bytes() == encoded:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def resolve_raw(raw: Path, identity: str | None) -> tuple[Path, str, Path]:
    if not raw.is_dir():
        raise ValueError(f'OCR directory does not exist: {raw}')
    raw = raw.resolve()
    if (raw / 'raw').is_dir():
        raw = raw / 'raw'
    # Exact item selection takes precedence when the root also has other items.
    if identity and (raw / identity).is_dir():
        folder = contained(raw, identity)
    else:
        lists = list(raw.rglob('*content_list.json'))
        if len(lists) == 1:
            # Standard item dirs may have one nested extraction folder.
            first = lists[0].relative_to(raw).parts[0]
            folder = raw if lists[0].parent == raw or raw.name == identity else raw / first
        elif identity:
            raise ValueError(f'No unambiguous OCR result for --id {identity!r} in {raw}')
        else:
            raise ValueError('Ambiguous OCR root; provide --id or a single item raw directory')
    read_items(folder)  # Use the same v1 contract/path validation as core.
    part_id = relative_name(folder.name, component=True)
    return folder.parent, part_id, folder


def run_command(command: list[str], *, timeout: int = 600) -> dict:
    record = {'command': command}
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=timeout)
        record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        record.update(returncode=-1, stdout='', stderr=str(exc))
    return record


def _hash_tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in sorted(root.rglob('*')) if p.is_file()}


def _outputs_unchanged(publisher: Publisher) -> bool:
    if not publisher.owned:
        return False
    for name, expected in publisher.owned.items():
        target = contained(publisher.root, name)
        if not target.is_file() or sha256_file(target) != expected:
            return False
    return True


def build(args: argparse.Namespace) -> tuple[int, dict]:
    pdf = Path(args.pdf).resolve()
    if not pdf.is_file():
        raise ValueError(f'PDF does not exist: {pdf}')
    out = Path(args.out).absolute()
    state = Path(args.state_dir).absolute() if args.state_dir else out.parent / f'.{out.name}.build'
    publisher = Publisher(out, state_root=state)
    # Check state ancestors before writing any external state, as Publisher does.
    for parent in (state, *state.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError(f'State path contains a link: {parent}')
    preflight = inspect_pdf(pdf)
    payload = load_export(Path(args.metadata).resolve() if args.metadata else None,
                          args.id, preflight['sha256'])
    identity = args.id or (export_identity(payload) if payload else None)
    if identity:
        relative_name(identity, component=True)
    raw_root, part_id, item_dir = resolve_raw(Path(args.raw), identity)
    identity = identity or part_id
    if (item_dir.is_relative_to(state.resolve()) or state.resolve().is_relative_to(item_dir)
            or out.resolve().is_relative_to(raw_root) or raw_root.is_relative_to(out.resolve())):
        raise ValueError('OCR input, output and per-document state must not contain one another')
    if pdf.is_relative_to(out.resolve()):
        raise ValueError('Source PDF must remain outside delivery directory')
    fields, provenance = collect_metadata(preflight, payload, crossref=args.crossref,
                                           cache_dir=contained(state, 'research/crossref-cache'))
    title = fields.get('title') or pdf.stem
    if 'title' not in fields:
        provenance['review'].append('Navigation/H1 uses source filename only; not asserted as publication title')
    if getattr(args, 'file', None):
        target_file = args.file if args.file.endswith('.md') else f'{args.file}.md'
    else:
        from research_metadata import format_zotero_basename
        extra_cand = preflight.get('pdf_properties') if isinstance(preflight.get('pdf_properties'), dict) else {}
        target_file = f'{format_zotero_basename(fields, pdf.stem, extra_candidates=extra_cand)}.md'
    manifest = {'source': str(pdf), 'pages': preflight['page_count'], 'parts': [
        {'id': part_id, 'start_page': 1, 'end_page': preflight['page_count']}]}
    chapters = [{'id': identity, 'title': title, 'file': target_file,
                 'start': 1, 'end': preflight['page_count']}]
    research = contained(state, 'research')
    manifest_path = contained(research, 'manifest.json')
    chapters_path = contained(research, 'chapters.json')
    metadata_path = contained(research, 'metadata.json')
    report_path = contained(research, 'research-report.json')
    # Include code, raw assets and repair bytes in the cache identity; never skip
    # ownership checks because a prior run happened to report success.
    fingerprint = {'pdf': preflight['sha256'], 'raw_path': str(item_dir), 'raw': _hash_tree(item_dir),
                   'metadata': fields, 'identity': identity, 'reading_order': args.reading_order,
                   'file': target_file,
                   'repairs': sha256_file(Path(args.repairs)) if args.repairs else None,
                   'code': {p.name: sha256_file(p) for p in sorted(SCRIPTS.glob('*.py'))},
                   'math_validator': sha256_file(SCRIPTS / 'validate_math.cjs'),
                   'keep_table_images': True}
    cache_key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    previous = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
    cache_hit = previous.get('cache_key') == cache_key and previous.get('automatic_checks') == 'pass' and _outputs_unchanged(publisher)
    write_json(contained(research, 'preflight.json'), preflight)
    write_json(contained(research, 'provenance.json'), provenance)
    write_json(manifest_path, manifest)
    write_json(chapters_path, chapters)
    write_json(metadata_path, {identity: fields})
    report = {'version': 1, 'id': identity, 'source_sha256': preflight['sha256'],
              'cache_key': cache_key, 'cache_hit': cache_hit,
              'ocr': {'action': 'reuse_existing_local_result', 'item_dir': str(item_dir),
                      'source_association': 'caller_supplied; OCR bytes alone cannot prove source identity'},
              'reading_order': args.reading_order, 'commands': [], 'automatic_checks': 'not_run',
              'acceptance': 'draft_pending_source_and_browser_review',
              'missing_metadata': provenance['missing_fields']}
    if not cache_hit:
        command = [sys.executable, str(SCRIPTS / 'build_markdown.py'), '--raw', str(raw_root),
                   '--out', str(out), '--pdf', str(pdf), '--manifest', str(manifest_path),
                   '--chapters', str(chapters_path), '--metadata-json', str(metadata_path),
                   '--reading-order', args.reading_order, '--keep-table-images', '--state-dir', str(state)]
        if args.repairs:
            command.extend(['--repairs', str(Path(args.repairs).resolve())])
        result = run_command(command)
        report['commands'].append(result)
        if result['returncode']:
            report['automatic_checks'] = 'build_failed'
            write_json(report_path, report)
            return 1, report
    else:
        report['build'] = 'reused_unmodified_owned_output'
    # Run validators even on cache hit, capturing every exit code and output.
    formulas_path = contained(research, 'formulas.json')
    math_report = contained(research, 'math-report.json')
    commands = [
        [sys.executable, str(SCRIPTS / 'validate_output.py'), str(out), '--images-dir', 'images', '--json'],
        [sys.executable, str(SCRIPTS / 'extract_formulas.py'), str(out), '--out', str(formulas_path)],
    ]
    for command in commands:
        report['commands'].append(run_command(command))
    extraction = report['commands'][-1]
    if extraction['returncode'] == 0 and formulas_path.is_file():
        formulas = json.loads(formulas_path.read_text(encoding='utf-8'))
        report['formula_count'] = len(formulas)
        command = ['node', str(SCRIPTS / 'validate_math.cjs'), str(formulas_path), '--report', str(math_report)]
        if not formulas:
            command.append('--allow-empty')
            report['zero_formulas'] = 'Extractor found zero formulas; no dummy formulas added; source coverage still requires review'
        report['commands'].append(run_command(command))
    report['automatic_checks'] = 'pass' if all(r['returncode'] == 0 for r in report['commands']) else 'fail'
    write_json(report_path, report)
    return (0 if report['automatic_checks'] == 'pass' else 1), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pdf', required=True, help='User-provided local source PDF')
    parser.add_argument('--raw', required=True, help='Single item dir, raw root, or workspace containing raw/')
    parser.add_argument('--metadata', help='Canonical object, Zotero item/data wrapper, or exported library JSON list')
    parser.add_argument('--out', required=True, help='Delivery directory for ONE PDF; contains academic Markdown document')
    parser.add_argument('--file', help='Explicit Markdown filename inside --out (default: academic {{firstCreator}} - {{year}} - {{title}}.md)')
    parser.add_argument('--naming', choices=['zotero', 'classic'], default='zotero',
                        help='Naming style (default: zotero pattern; standard academic naming)')
    parser.add_argument('--state-dir', help='External state directory (default sibling .<out-name>.build)')
    parser.add_argument('--repairs', help='Source-verified repairs forwarded unchanged to core')
    parser.add_argument('--id', help='Stable document ID; required for ambiguous multi-item metadata/OCR roots')
    parser.add_argument('--reading-order', choices=['source', 'geometry'], default='source',
                        help='Default preserves OCR sequence within each physical page')
    parser.add_argument('--crossref', action='store_true', help='Opt in to DOI-only public lookup; strict title/DOI gate')
    args = parser.parse_args(argv)
    try:
        code, report = build(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'Research build failed: {exc}', file=sys.stderr)
        return 1
    from console_output import safe_print
    safe_print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
