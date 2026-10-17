#!/usr/bin/env python
"""教材插图多面板审查与重组修复辅助工具。

功能：
1. 遍历 MinerU OCR 产物中的图片与图表块；
2. 自动检测同一页面内碎片化多面板图块，计算外接矩形（union bbox）；
3. 识别吞并多图号题注、题注脱落与阅读顺序倒错候选；
4. 生成符合 build_markdown --repairs 契约的修复规则草稿与审查台账。

用法：
    python scripts/audit_figures.py <raw目录或ocr-work目录> [--manifest manifest.json] \
        [--out-repairs repairs-draft.json] [--out-report audit-report.json]

无 manifest 时页码为各文档本地页码，来源以 raw 内的相对清单路径标识。
多文档只支持 --out-report（含逐来源草稿）；--out-repairs 须指定单文档目录
或提供映射到同一本原 PDF 的 manifest。所有建议均须人工回看原页确认。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import math
import collections
import json
import re
from pathlib import Path
from build_support import contained, relative_name
from console_output import safe_print as print

from figure_identifiers import caption_keys
from portable_paths import safe_path, walk_files, read_text

PANEL_TAG_PATTERN = re.compile(r'^\s*[（(]([a-zA-Z])[）)](?:\s|$)')
PANEL_EVIDENCE = re.compile(r'[（(]([a-zA-Z])[）)]')


def _valid_box(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000)


def _neighbors(left, right):
    if not _valid_box(left) or not _valid_box(right) or left == right:
        return False
    wx = min(left[2] - left[0], right[2] - right[0])
    hy = min(left[3] - left[1], right[3] - right[1])
    ox = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    oy = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    dx = max(0, max(left[0], right[0]) - min(left[2], right[2]))
    dy = max(0, max(left[1], right[1]) - min(left[3], right[3]))
    return (oy / hy >= .7 and dx <= min(100, wx * .5)
            or ox / wx >= .7 and dy <= min(100, hy * .5))


def _snapshot(block):
    typ = block['type']
    return {key: deepcopy(block.get(key, default)) for key, default in (
        ('type', None), ('img_path', ''), ('bbox', None),
        (f'{typ}_caption', []), (f'{typ}_footnote', []))}


def extract_captions(block: dict) -> list[str]:
    captions = block.get('image_caption') or block.get('chart_caption') or []
    return [captions] if isinstance(captions, str) else list(captions)


def extract_footnotes(block: dict) -> list[str]:
    notes = block.get('image_footnote') or block.get('chart_footnote') or []
    return [notes] if isinstance(notes, str) else list(notes)


def extract_figure_numbers(captions: list[str]) -> list[str]:
    """Recognize only explicit caption labels with shared namespace/kind rules."""
    return caption_keys(captions)


def box_overlap_area(b1: list[float], b2: list[float]) -> float:
    x0 = max(b1[0], b2[0])
    y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2])
    y1 = min(b1[3], b2[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def box_area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def audit_page_images(page_num: int, blocks: list[dict], confident_only: bool = False) -> tuple[list[dict], list[dict], list[dict]]:
    """Audit images on a single page, proposing candidate clusters and flagging anomalies.

    If ``confident_only`` is True, only high-confidence repairs (unambiguous panel clustering,
    reasonable bounding box, no conflicting multi-figure numbers) are returned.
    """
    images = [(i, b) for i, b in enumerate(blocks) if b.get('type') in ('image', 'chart')]
    if not images:
        return [], [], []

    page_repairs = []
    page_anomalies = []
    inventory = []

    # Map figure numbers on this page
    by_num = collections.defaultdict(list)
    unnumbered = []
    for idx, img in images:
        caps = extract_captions(img)
        notes = extract_footnotes(img)
        nums = extract_figure_numbers(caps)
        bbox = img.get('bbox') or [0, 0, 0, 0]
        item = {
            'page': page_num,
            'index': idx,
            'bbox': bbox,
            'type': img.get('type'),
            'captions': caps,
            'numbers': nums,
        }
        inventory.append(item)

        if len(nums) > 1:
            page_anomalies.append({
                'page': page_num,
                'index': idx,
                'kind': 'multiple_figure_numbers',
                'numbers': nums,
                'captions': caps,
            })

        if nums:
            for n in nums:
                by_num[n].append((idx, img))
        else:
            unnumbered.append((idx, img))

    # A panel must carry an explicit label and be close to exactly one numbered
    # group. Empty captions and remote same-row figures are not panel evidence.
    for u_idx, u_img in unnumbered:
        caps = extract_captions(u_img)
        if not caps or not all(PANEL_TAG_PATTERN.match(c) for c in caps):
            continue
        candidates = [num for num, grp in by_num.items() if any(
            _neighbors(u_img.get('bbox'), block.get('bbox')) for _, block in grp)]
        if len(candidates) == 1:
            by_num[candidates[0]].append((u_idx, u_img))
            by_num[candidates[0]].sort(key=lambda pair: pair[0])

    # Cluster images sharing the same figure number
    for num, group in by_num.items():
        if len(group) > 1:
            indices = [idx for idx, _ in group]
            boxes = [b['bbox'] for _, b in group if _valid_box(b.get('bbox'))]
            if boxes:
                union_b = [
                    max(0, int(min(b[0] for b in boxes)) - 3),
                    max(0, int(min(b[1] for b in boxes)) - 3),
                    min(1000, int(max(b[2] for b in boxes)) + 3),
                    min(1000, int(max(b[3] for b in boxes)) + 3),
                ]
                # Combined caption
                all_caps = []
                for _, b in group:
                    for c in extract_captions(b):
                        if c not in all_caps and not re.fullmatch(r'\s*[（(][a-zA-Z][）)]\s*', c):
                            all_caps.append(c)

                first_idx, first_block = group[0]
                typ = first_block.get('type', 'image')
                old_snap = _snapshot(first_block)

                # Distinct explicit panel labels or the same caption identity on
                # every fragment, connected geometry, and no intervening prose.
                numbered = all(extract_figure_numbers(extract_captions(b)) == [num]
                               for _, b in group)
                tags = [set(PANEL_EVIDENCE.findall(' '.join(extract_captions(b))))
                        for _, b in group]
                panel_evidence = all(tags) and len(set.union(*tags)) >= len(group)
                reached = {0}
                while True:
                    added = {j for j, (_, block) in enumerate(group) if j not in reached
                             and any(_neighbors(block.get('bbox'), group[k][1].get('bbox'))
                                     for k in reached)}
                    if not added:
                        break
                    reached.update(added)
                has_anomaly = any(len(extract_figure_numbers(extract_captions(b))) > 1
                                  for _, b in group)
                intervening = any(blocks[j].get('type') not in ('image', 'chart')
                                  and str(blocks[j].get('text', '')).strip()
                                  for j in range(min(indices), max(indices)))
                foreign_inside = any(idx not in indices and _valid_box(b.get('bbox'))
                                     and box_overlap_area(b['bbox'], union_b) > 0
                                     for idx, b in images)
                text_inside = any(
                    idx not in indices and _valid_box(block.get('bbox'))
                    and block.get('type') in ('text', 'body_text', 'equation', 'table', 'list')
                    and box_overlap_area(block['bbox'], union_b) > 0
                    and not any(box_overlap_area(block['bbox'], box) >= .9 * box_area(block['bbox'])
                                for box in boxes)
                    for idx, block in enumerate(blocks))
                unique_locations = all(sum(block.get('bbox') == b.get('bbox') for block in blocks) == 1
                                       for _, b in group)
                is_confident = (not has_anomaly and not intervening and not foreign_inside and not text_inside
                                and unique_locations and not any(b.get('_figure_boundary') for _, b in group)
                                and all(_valid_box(b.get('bbox')) for _, b in group)
                                and len(reached) == len(group)
                                and 0 < box_area(union_b) <= 920000
                                and sum(box_area(box) for box in boxes) >= .5 * box_area(union_b)
                                and (numbered or panel_evidence))
                confidence = 'high' if is_confident else 'manual_review'
                if confident_only and not is_confident:
                    continue

                reason = (f'待原页人工确认：第{page_num}页图号标识 {num} 的{len(group)}个图块'
                          '可能为碎片面板；候选外接框及碎块移除仅为草稿，尚未核实')
                all_notes = list(dict.fromkeys(note for _, b in group for note in extract_footnotes(b)))
                page_repairs.append({
                    'page': page_num,
                    'bbox': first_block.get('bbox'),
                    'action': 'update_image',
                    'old': old_snap,
                    'new_bbox': union_b,
                    'image_caption': all_caps,
                    'image_footnote': all_notes,
                    'confidence': confidence,
                    'reason': reason,
                })
                for idx, b in group[1:]:
                    page_repairs.append({
                        'page': page_num,
                        'bbox': b.get('bbox'),
                        'action': 'skip',
                        'old': b.get('text', ''),
                        'confidence': confidence,
                        'reason': reason,
                    })

    # Reading order anomalies: figure index appears before earlier text in physical y
    for idx, img in images:
        b_box = img.get('bbox') or [0, 0, 0, 0]
        later_text_above = [
            {'index': j, 'bbox': blk.get('bbox')}
            for j, blk in enumerate(blocks)
            if j > idx and blk.get('type') in ('text', 'equation', 'list', 'table')
            and blk.get('bbox') and blk['bbox'][3] + 8 <= b_box[1]
        ]
        if later_text_above:
            page_anomalies.append({
                'page': page_num,
                'index': idx,
                'kind': 'inverted_reading_order',
                'bbox': b_box,
                'later_text_above_count': len(later_text_above),
            })

    return inventory, page_repairs, page_anomalies


def generate_confident_repairs_for_blocks(blocks: list[dict]) -> list[dict]:
    """Generate high-confidence figure repair directives directly from blocks.

    Can be called by build_markdown.py or build_research_document.py to automatically
    stitch split multi-panel figures without requiring pre-generated repairs JSON.
    """
    by_page = collections.defaultdict(list)
    for b in blocks:
        page = b.get('_page') if b.get('_page') is not None else (b.get('page_idx', 0) + 1)
        by_page[page].append(b)

    all_repairs = []
    for page in sorted(by_page):
        _, reps, _ = audit_page_images(page, by_page[page], confident_only=True)
        for record in reps:
            record['reason'] = ('自动高置信度候选：同编号或独立面板标签，紧邻连通几何，无冲突图号与跨图正文；'
                                '按完整外接框回源重裁，内容仍须原页复核')
        all_repairs.extend(reps)
    return all_repairs


def audit_workspace(root: Path, manifest_path: Path | None = None, confident_only: bool = False) -> tuple[dict, list[dict]]:
    """Report each OCR source explicitly; return a flat draft only if unambiguous.

    Without a manifest, content lists are independent documents with local pages.
    Multiple documents keep their drafts in report['sources']; the returned flat
    list is empty, since build_markdown repairs cannot express source identity.
    """
    root = root.resolve()
    raw_dir = root / 'raw' if (root / 'raw').is_dir() else root
    if not safe_path(raw_dir).is_dir():
        raise ValueError(f'OCR directory does not exist: {raw_dir}')
    manifest = None
    selected_manifest = manifest_path if manifest_path is not None else root / 'manifest.json'
    if manifest_path is not None or selected_manifest.is_file():
        manifest = json.loads(read_text(selected_manifest))
        if not isinstance(manifest, dict) or not isinstance(manifest.get('parts'), list):
            raise ValueError('manifest requires a parts list')

    parts_to_scan = []
    if manifest is not None:
        seen_ids = set()
        for part in manifest['parts']:
            part_id = relative_name(part['id'], component=True)
            if part_id in seen_ids:
                raise ValueError(f'duplicate manifest part id: {part_id}')
            seen_ids.add(part_id)
            start_page = part.get('start_page')
            if type(start_page) is not int or start_page < 1:
                raise ValueError(f'manifest start_page must be a positive integer: {part_id}')
            part_folder = contained(raw_dir, part_id)
            # Match the builder's unversioned content-list contract. Multiple
            # candidates must not depend on filesystem enumeration order.
            clists = sorted([p for p in walk_files(part_folder, '*content_list.json') if not p.name.endswith('_v2.json')])
            if len(clists) != 1:
                raise ValueError(f'{part_folder}: expected one content_list.json, found {len(clists)}')
            clist = contained(raw_dir, clists[0].relative_to(raw_dir).as_posix())
            parts_to_scan.append((part_id, start_page, clist, part.get('end_page')))
    else:
        for clist in sorted([p for p in walk_files(raw_dir, '*content_list.json') if not p.name.endswith('_v2.json')]):
            # A full relative filename distinguishes same-basename folders and
            # files, remains stable when another document is added, and is
            # identical whether the caller supplies ocr-work or ocr-work/raw.
            source_id = clist.relative_to(raw_dir).as_posix()
            parts_to_scan.append((source_id, 1, contained(raw_dir, source_id), None))

    total_inventory = []
    all_repairs = []
    all_anomalies = []
    sources = []
    manifest_pages = {}

    for source_id, start_page, clist_path, end_page in parts_to_scan:
        data = json.loads(read_text(clist_path))
        if not isinstance(data, list) or any(not isinstance(block, dict) for block in data):
            raise ValueError(f'invalid OCR content list: {clist_path}')
        if end_page is not None and (type(end_page) is not int or end_page < start_page):
            raise ValueError(f'invalid manifest end_page: {source_id}')

        by_page = collections.defaultdict(list)
        for block in data:
            pi = block.get('page_idx', 0)
            if type(pi) is not int or pi < 0:
                raise ValueError(f'invalid page_idx in {source_id}: {pi!r}')
            if end_page is not None and start_page + pi > end_page:
                raise ValueError(f'page_idx exceeds manifest range: {source_id}')
            by_page[pi].append(block)

        inventory, repairs, anomalies = [], [], []
        for pi in sorted(by_page):
            page = start_page + pi
            if manifest is not None:
                if page in manifest_pages:
                    raise ValueError(f'ambiguous manifest page {page}: '
                                     f'{manifest_pages[page]} and {source_id}')
                manifest_pages[page] = source_id
            inv, reps, anoms = audit_page_images(page, by_page[pi], confident_only=confident_only)
            # Keep provenance in the report, not in the strict repair records.
            inventory.extend({**item, 'source_id': source_id} for item in inv)
            repairs.extend(reps)
            anomalies.extend({**item, 'source_id': source_id} for item in anoms)

        sources.append({
            'source_id': source_id,
            'content_list': clist_path.relative_to(raw_dir).as_posix(),
            'start_page': start_page,
            'page_numbering': 'manifest' if manifest is not None else 'document-local',
            'inventory': inventory,
            'repairs': repairs,
            'anomalies': anomalies,
        })
        total_inventory.extend(inventory)
        all_repairs.extend(repairs)
        all_anomalies.extend(anomalies)

    repair_output_allowed = manifest is not None or len(sources) <= 1
    page_keys = {(None if manifest is not None else item['source_id'], item['page'])
                 for item in total_inventory}
    report = {
        'total_figures_and_charts': len(total_inventory),
        'proposed_repairs': len(all_repairs),
        'anomalies_detected': len(all_anomalies),
        'anomalies': all_anomalies,
        'repair_status': 'auto_confident_applied' if confident_only else 'draft_requires_manual_source_confirmation',
        'repair_output_allowed': repair_output_allowed,
        'sources': sources,
        'inventory_summary': {
            'parts_scanned': len(parts_to_scan),
            'unique_pages_with_figures': len(page_keys),
        },
    }
    return report, all_repairs if repair_output_allowed else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', help='raw 数据目录或 ocr-work 根目录')
    parser.add_argument('--manifest', help='可选 manifest.json 路径')
    parser.add_argument('--out-repairs', help='输出待人工回源确认的草稿；无 manifest 时仅支持单文档')
    parser.add_argument('--out-report', help='输出含来源路径、本地/全局页码及逐来源草稿的审查报告 JSON')
    parser.add_argument('--confident-only', action='store_true', help='仅导出高置信度可自动缝合的修复规则')
    args = parser.parse_args()

    target = Path(args.path).resolve()
    manifest = Path(args.manifest).resolve() if args.manifest else None
    try:
        report, repairs = audit_workspace(target, manifest, confident_only=args.confident_only)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.out_repairs and not report['repair_output_allowed']:
        parser.error('--out-repairs requires a single document directory or a manifest; '
                     'multiple independent documents use --out-report for per-source drafts')

    print('=' * 60)
    print(f'插图审查完成：扫描图表 {report["total_figures_and_charts"]} 处，'
          f'涵盖 {report["inventory_summary"]["unique_pages_with_figures"]} 个页面')
    print(f'生成多面板重组草稿 {report["proposed_repairs"]} 项，'
          f'发现异常/倒错候选 {report["anomalies_detected"]} 处；均须人工回源确认')
    if not report['repair_output_allowed']:
        print('独立多文档：页码按来源分别计算，草稿仅保存在报告的 sources 中')
    print('=' * 60)

    if args.out_repairs:
        out_r = Path(args.out_repairs).resolve()
        out_r.parent.mkdir(parents=True, exist_ok=True)
        out_r.write_text(json.dumps(repairs, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'修复建议已写入: {out_r}')

    if args.out_report:
        out_rep = Path(args.out_report).resolve()
        out_rep.parent.mkdir(parents=True, exist_ok=True)
        out_rep.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'审查总账已写入: {out_rep}')


if __name__ == '__main__':
    main()
