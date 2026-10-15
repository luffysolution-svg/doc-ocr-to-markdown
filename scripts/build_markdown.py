#!/usr/bin/env python
"""把 OCR 结果装配成 Markdown 知识库。

这是流程的核心脚本：读取 MinerU 的 content_list.json，按页面顺序重建正文，
把公式转成 Obsidian 兼容 LaTeX，图片替换为本地高清图，并可选地用源文件
（PPTX）的原生文字校正 OCR 错字、用内嵌原图替换低清截图。

用法：
    # 最基本：只用 OCR 结果
    python build_markdown.py --raw ./ocr-out/raw --out ./知识库

    # 带源文件校正（推荐，PPTX 源文件时）
    python build_markdown.py --raw ./ocr-out/raw --out ./知识库 \\
        --source-dir ./课件 --source-glob '*.pptx'

    # 只处理指定章（配合 --id-prefix 或逐目录跑）
    python build_markdown.py --raw ./ocr-out/raw --out ./知识库 --only ch01 ch02

输出结构：
    知识库/
      00-目录.md
      01-第一章.md ...
      images/ch01/figure-001.png ...
      build-report.json          各章统计与待人工确认项
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
from collections import defaultdict, deque
from difflib import SequenceMatcher
from pathlib import Path
from unicodedata import normalize

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_math import normalize_math, plain_text, split_leading_prose

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
except ImportError:
    Presentation = None

# 页眉页脚等已知的重复元素（按需增补）
SKIP_TEXT = {'材料分析测试方法'}
# 纯序号标签，如图中 "(a)" "(1)" "a."
LABEL_ONLY = re.compile(r'^[（(]?[a-zA-Z0-9][）)]?[.、]?$')
# 图片说明匹配
CAPTION = re.compile(r'^\s*(图|表|Fig|Table)\s*[\d一二三四五六七八九十]')


def nkey(text: str) -> str:
    """归一化用于比较：去掉空白与标点，只留可比较字符。"""
    text = normalize('NFKC', text).casefold()
    return ''.join(c for c in text if c.isalnum() or '\u4e00' <= c <= '\u9fff')


def box_of(item: dict) -> tuple[float, float, float, float]:
    """MinerU bbox 是 0-1000 归一化坐标。"""
    box = item.get('bbox') or [0, 0, 0, 0]
    return (box[0] / 1000, box[1] / 1000, box[2] / 1000, box[3] / 1000)


def overlap(inner, outer) -> float:
    """inner 被 outer 覆盖的面积比例。"""
    x0, y0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x1, y1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return 0.0 if area <= 0 else (x1 - x0) * (y1 - y0) / area


def union_box(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


# ---------------------------------------------------------------- 源文件（PPTX）

def flatten_paragraphs(lines: list[str]) -> str:
    """把 PPT 文本框里的硬换行还原成连贯段落。"""
    if not lines:
        return ''
    flat = lines[0]
    for line in lines[1:]:
        if re.match(r'^(?:[（(]?\d+[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|[A-Za-z][.、)）])', line) or re.match(r'^\d+[.、]', line):
            flat += '\n' + line
        elif re.search(r'[A-Za-z0-9]$', flat) and re.match(r'^[A-Za-z0-9]', line):
            flat += ' ' + line
        else:
            flat += line
    return flat


def load_source(path: Path) -> list[dict]:
    """读 PPTX：每页返回原生文本框和图片（含二进制）。"""
    if Presentation is None:
        raise RuntimeError('需要 python-pptx：pip install python-pptx')
    prs = Presentation(path)
    sw, sh = prs.slide_width, prs.slide_height
    pages = []
    for slide in list(prs.slides):
        texts, pictures = [], []
        for shape in slide.shapes:
            box = (shape.left / sw, shape.top / sh,
                   (shape.left + shape.width) / sw, (shape.top + shape.height) / sh)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and shape.width >= sw * 0.035 and shape.height >= sh * 0.025:
                blob = shape.image.blob
                pictures.append({'box': box, 'ext': shape.image.ext, 'blob': blob})
            elif shape.has_text_frame and shape.text.strip():
                lines = [re.sub(r'[ \t]+', ' ', x).strip()
                         for x in shape.text_frame.text.split('\n')]
                text = flatten_paragraphs([x for x in lines if x])
                if not text or text in SKIP_TEXT:
                    continue
                texts.append({'box': box, 'text': text, 'key': nkey(text)})
        pages.append({'texts': texts, 'pictures': pictures})
    return pages


def best_text_match(block: dict, texts: list[dict], used: set, key: str):
    """按位置重叠 + 相似度找源文件里对应的文本框。"""
    box = box_of(block)
    best = None
    for index, cand in enumerate(texts):
        if index in used or not cand['key']:
            continue
        cover = overlap(box, cand['box'])
        if cover < 0.5:
            continue
        sim = SequenceMatcher(None, key, cand['key']).ratio()
        score = cover * 0.4 + sim * 0.6
        if best is None or score > best[0]:
            best = (score, index, cand, cover, sim)
    return best


def best_picture_match(box, pictures: list[dict], used: set):
    best = None
    for index, pic in enumerate(pictures):
        if index in used:
            continue
        score = max(overlap(box, pic['box']), overlap(pic['box'], box))
        if best is None or score > best[0]:
            best = (score, index, pic)
    if best and best[0] >= 0.35:
        return best
    return None


# ---------------------------------------------------------------- 图片落盘

class FigureStore:
    """图片按内容哈希命名，天然去重。"""

    def __init__(self, image_dir: Path):
        self.image_dir = image_dir
        self.emitted: set[str] = set()
        self.count = 0

    def store(self, blob: bytes, ext: str) -> str | None:
        digest = hashlib.sha1(blob).hexdigest()[:12]
        name = f'{digest}.{"jpg" if ext.lower() in ("jpg", "jpeg") else "png"}'
        target = self.image_dir / name
        if not target.exists():
            if ext.lower() in ('png', 'jpg', 'jpeg'):
                target.write_bytes(blob)
            elif Image is not None:
                with Image.open(io.BytesIO(blob)) as im:
                    im.convert('RGB').save(target)
            else:
                return None
        return name

    def crop(self, pdf_page, box: tuple, scale: int = 1920) -> bytes | None:
        """从 PDF 页面按归一化坐标高清裁图（源文件里没有这张图时的退路）。"""
        try:
            import fitz
        except ImportError:
            return None
        x0 = max(0.0, box[0]) * pdf_page.rect.width
        y0 = max(0.0, box[1]) * pdf_page.rect.height
        x1 = min(1.0, box[2]) * pdf_page.rect.width
        y1 = min(1.0, box[3]) * pdf_page.rect.height
        if x1 <= x0 or y1 <= y0:
            return None
        factor = scale / pdf_page.rect.width
        pix = pdf_page.get_pixmap(matrix=fitz.Matrix(factor, factor),
                                  clip=fitz.Rect(x0, y0, x1, y1), alpha=False)
        return pix.tobytes('png')


# ---------------------------------------------------------------- 主装配

def captions_of(items: list[dict]) -> list[str]:
    values = []
    for item in items:
        for field in ('image_caption', 'chart_caption', 'image_footnote', 'chart_footnote'):
            content = item.get(field) or []
            if isinstance(content, str):
                content = [content]
            for entry in content:
                text = str(entry).strip()
                if text and not LABEL_ONLY.match(text):
                    values.append(text)
    labeled = [v for v in values if CAPTION.match(v)]
    result = []
    for text in labeled + values:
        if text not in result:
            result.append(text)
    return result


def table_markdown(html: str) -> str:
    """MinerU 的 table_body 是 HTML，转成 Markdown 表格。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table')
    if not table:
        return ''
    grid, spans = [], {}
    for ri, tr in enumerate(table.find_all('tr')):
        row = {c: v for (r, c), v in spans.items() if r == ri}
        ci = 0
        for cell in tr.find_all(['td', 'th'], recursive=False):
            while ci in row:
                ci += 1
            value = re.sub(r'\s+', ' ', cell.get_text(' ', strip=True)).replace('|', r'\vert')
            rowspan, colspan = int(cell.get('rowspan', 1)), int(cell.get('colspan', 1))
            for dx in range(colspan):
                row[ci + dx] = value if dx == 0 else ''
                for dy in range(1, rowspan):
                    spans[(ri + dy, ci + dx)] = value if dx == 0 else ''
            ci += colspan
        grid.append(row)
    width = max((max(r, default=-1) + 1 for r in grid), default=0)
    if not width:
        return ''
    rows = [[r.get(i, '') for i in range(width)] for r in grid]
    out = ['| ' + ' | '.join(rows[0]) + ' |',
           '| ' + ' | '.join(['---'] * width) + ' |']
    out += ['| ' + ' | '.join(r) + ' |' for r in rows[1:]]
    return '\n'.join(out)


def leading_number(text: str) -> int | None:
    """取出名字里的首个数字，用于把 data_id 与源文件配对（ch01 ↔ 01 第一章.pptx）。"""
    match = re.search(r'\d+', text)
    return int(match.group()) if match else None


def match_source(folder_name: str, sources: dict[str, Path], ordinal: int) -> Path | None:
    """把 OCR 结果目录与源文件配对。

    依次尝试：名字互相包含 → 首个数字相同 → 按顺序对应。
    只靠字符串包含不够用：data_id 常是 ch01，而源文件名是 "01 第一章.pptx"。
    """
    for key, path in sources.items():
        if key and (key in folder_name or folder_name in key):
            return path
    number = leading_number(folder_name)
    if number is not None:
        for key, path in sources.items():
            if leading_number(key) == number:
                return path
    items = list(dict.fromkeys(sources.values()))
    if 0 <= ordinal < len(items):
        return items[ordinal]
    return None


def build_chapter(items: list[dict], source_pages: list[dict] | None, pdf_path: Path | None,
                  image_dir: Path, title: str, review: list,
                  ocr_dir: Path | None = None) -> tuple[str, dict]:
    store = FigureStore(image_dir)
    content = [f'# {title}']
    headings: set[str] = set()
    last_heading = None
    stats = defaultdict(int)
    pdf = None
    if pdf_path and pdf_path.exists():
        try:
            import fitz
            pdf = fitz.open(pdf_path)
        except ImportError:
            pdf = None

    by_page = defaultdict(list)
    for item in items:
        by_page[item.get('page_idx', 0)].append(item)

    # 文档标题：优先用页面里检测到的第一个标题，其次用传入的 title
    doc_title = title
    for item in items:
        if item.get('text_level') and item.get('text'):
            candidate = re.sub(r'\s+', ' ', str(item['text'])).strip()
            candidate = plain_text(candidate) if ('\\' in candidate or '$' in candidate) else candidate
            if candidate and nkey(candidate) != nkey(title):
                doc_title = candidate
                break
    if doc_title != title:
        content = [f'# {doc_title}']
    else:
        content = [f'# {title}']
    title = doc_title

    for pi in sorted(by_page):
        page_items = by_page[pi]
        source = source_pages[pi] if source_pages and pi < len(source_pages) else None
        used_text: set[int] = set()
        used_pic: set[int] = set()
        # 图片占据的区域：源文件的图 + OCR 识别出的图块，文字落在里面就是图内标签
        picture_regions = [p['box'] for p in source['pictures']] if source else []
        picture_regions += [box_of(i) for i in page_items if i.get('type') in ('image', 'chart')]
        figure_blocks: list[str] = []
        emit: list[str] = []

        for item in page_items:
            kind = item.get('type')
            if kind in ('header', 'footer', 'page_number', 'page_footnote', 'discarded'):
                continue

            # ---- 图片 ----
            if kind in ('image', 'chart'):
                box = box_of(item)
                # 合并同页相邻/重叠的图片块（一幅图常被切成多块）
                group = [item]
                for other in page_items:
                    if other is item or other.get('type') not in ('image', 'chart'):
                        continue
                    ob = box_of(other)
                    if overlap(ob, box) >= 0.35 or overlap(box, ob) >= 0.35:
                        group.append(other)
                for member in group:
                    if member is not item and member in page_items:
                        page_items.remove(member)

                name = None
                match = best_picture_match(box, source['pictures'], used_pic) if source else None
                if match:
                    used_pic.add(match[1])
                    name = store.store(match[2]['blob'], match[2]['ext'])
                    stats['native_figures'] += 1
                elif pdf is not None and pi < len(pdf):
                    blob = store.crop(pdf[pi], box)
                    if blob:
                        name = store.store(blob, 'png')
                        stats['cropped_figures'] += 1
                elif ocr_dir is not None and item.get('img_path'):
                    # 最后回退：直接用 OCR 结果自带的切图（通常是页面截图，清晰度较低）
                    candidate = ocr_dir / str(item['img_path']).replace('\\', '/')
                    if candidate.is_file():
                        name = store.store(candidate.read_bytes(), candidate.suffix.lstrip('.'))
                        stats['ocr_figures'] += 1

                if not name or name in store.emitted:
                    continue
                store.emitted.add(name)
                store.count += 1
                caps = captions_of(group)
                alt = plain_text(caps[0]) if caps else title
                alt = re.sub(r'[\[\]\n|]', ' ', alt).strip()
                # 图片块插在所在位置
                figure_blocks.append(f'![{alt}](images/{image_dir.name}/{name})')
                for cap in caps:
                    if nkey(cap) not in headings:
                        figure_blocks.append(cap)
                stats['figures'] += 1
                continue

            # ---- 正文 ----
            if kind == 'text':
                raw = item.get('text', '')
                text = re.sub(r'[ \t]+', ' ', raw).strip()
                if not text or text in SKIP_TEXT or LABEL_ONLY.match(text):
                    continue
                # 画在图片上的标签：图片文件里已包含，不重复成正文
                if picture_regions and len(text) <= 12:
                    item_box = box_of(item)
                    # 逐个区域判断（不能用并集，并集可能覆盖整页）
                    if any(overlap(item_box, region) > 0.6 for region in picture_regions):
                        stats['embedded_labels'] += 1
                        continue

                is_heading = bool(item.get('text_level')) or (box_of(item)[1] < 0.152 and len(text) < 100 and '$' not in text)
                if is_heading:
                    if nkey(text) == nkey(title):
                        continue
                    identity = nkey(text)
                    if identity == last_heading or identity in headings:
                        last_heading = identity
                        continue
                    level = 2
                    m = re.match(r'^(\d+(?:\.\d+)*)', text)
                    if m:
                        level = min(5, len(m.group(1).split('.')))
                    disp = plain_text(text) if ('\\' in text or '$' in text) else text
                    emit.append('#' * level + ' ' + disp)
                    headings.add(identity)
                    last_heading = identity
                    stats['headings'] += 1
                    continue

                # 用源文件文字校正（相似度足够高才替换）
                if source and '$' not in text:
                    key = nkey(text)
                    match = best_text_match(item, source['texts'], used_text, key)
                    if match:
                        used_text.add(match[1])
                        ratio = len(match[2]['key']) / max(1, len(key))
                        if 0.9 <= ratio <= 1.1 and match[4] >= 0.9 and match[2]['key'] != key:
                            review.append({'page': pi + 1, 'applied': True,
                                           'similarity': round(match[4], 3),
                                           'ocr': text, 'source': match[2]['text']})
                            text = match[2]['text']
                            stats['text_corrected'] += 1
                        elif 0.5 <= ratio <= 2.0 and match[2]['key'] != key and match[4] >= 0.5:
                            review.append({'page': pi + 1, 'applied': False,
                                           'similarity': round(match[4], 3),
                                           'ocr': text, 'source': match[2]['text']})
                            stats['text_review'] += 1

                if nkey(text) != last_heading:
                    emit.append(text)
                    stats['paragraphs'] += 1
                continue

            # ---- 公式 ----
            if kind == 'equation':
                body = normalize_math(item.get('text', ''))
                if not body:
                    continue
                leading, body, trailing = split_leading_prose(body)
                if leading:
                    emit.append(leading + (trailing or ''))
                    stats['equation_captions'] += 1
                body = body.strip()
                if not body:
                    continue
                if body.startswith('$$'):
                    body = body[2:-2].strip()
                emit.append(f'$$\n{body}\n$$')
                stats['equations'] += 1
                continue

            # ---- 表格 ----
            if kind == 'table':
                for cap in captions_of([item]):
                    emit.append(cap)
                md = table_markdown(item.get('table_body', ''))
                if md:
                    emit.append(md)
                    stats['tables'] += 1
                for note in (item.get('table_footnote') or []):
                    if isinstance(note, str) and note.strip():
                        emit.append(note.strip())
                continue

            # ---- 列表 / 代码 ----
            if kind == 'list':
                for entry in item.get('list_items', []):
                    if str(entry).strip():
                        emit.append('- ' + str(entry).strip())
                continue
            if kind in ('code', 'algorithm'):
                body = item.get('code_body') or item.get('text', '')
                if body:
                    emit.append(f'```\n{body}\n```')
                continue
            if item.get('text'):
                text = re.sub(r'[ \t]+', ' ', str(item['text'])).strip()
                if text and not LABEL_ONLY.match(text):
                    emit.append(text)

        content.extend(figure_blocks)
        content.extend(emit)

    if pdf:
        pdf.close()

    text = '\n\n'.join(b for b in content if b and b.strip())
    text = re.sub(r'\n{3,}', '\n\n', text)
    stats['figures'] = store.count
    return text.strip() + '\n', dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description='把 OCR 结果装配成 Markdown 知识库')
    parser.add_argument('--raw', required=True, help='OCR 结果目录（含各文件的 content_list.json）')
    parser.add_argument('--out', required=True, help='输出目录')
    parser.add_argument('--source-dir', help='源文件目录（用于文字校正与内嵌原图，如 PPTX）')
    parser.add_argument('--source-glob', default='*.pptx')
    parser.add_argument('--pdf-dir', help='PDF 目录（源文件里没有的图从这里裁切）')
    parser.add_argument('--only', nargs='*', help='只处理这些子目录（data_id）')
    parser.add_argument('--titles', help='JSON 文件：{"data_id": "章节标题"}')
    args = parser.parse_args()

    raw_root = Path(args.raw).resolve()
    out_root = Path(args.out).resolve()
    if out_root.exists():
        shutil.rmtree(out_root)
    (out_root / 'images').mkdir(parents=True)

    titles = json.loads(Path(args.titles).read_text(encoding='utf-8')) if args.titles else {}
    sources = {}
    if args.source_dir:
        for path in sorted(Path(args.source_dir).glob(args.source_glob)):
            stem = re.sub(r'^\d+[\s\-_]*', '', path.stem).strip()
            sources[path.stem] = path
            sources[stem] = path

    report, review_all = [], {}
    folders = sorted(d for d in raw_root.iterdir() if d.is_dir())
    if args.only:
        folders = [d for d in folders if d.name in set(args.only)]
    if not folders:
        raise SystemExit(f'{raw_root} 下没有找到结果目录')

    for ordinal, folder in enumerate(folders):
        lists = list(folder.glob('*_content_list.json'))
        if not lists:
            print(f'跳过 {folder.name}（无 content_list.json）')
            continue
        items = json.loads(lists[0].read_text(encoding='utf-8'))
        title = titles.get(folder.name, folder.name)
        image_dir = out_root / 'images' / folder.name
        image_dir.mkdir(parents=True, exist_ok=True)

        source_pages = None
        source_name = None
        source_path = match_source(folder.name, sources, ordinal) if sources else None
        if source_path:
            source_pages = load_source(source_path)
            source_name = source_path.name

        pdf_path = None
        if args.pdf_dir:
            for candidate in Path(args.pdf_dir).glob('*.pdf'):
                if (source_name and candidate.stem == source_name) or folder.name in candidate.stem:
                    pdf_path = candidate
                    break
            if pdf_path is None:
                # 再按首个数字配对
                number = leading_number(folder.name)
                for candidate in Path(args.pdf_dir).glob('*.pdf'):
                    if number is not None and leading_number(candidate.stem) == number:
                        pdf_path = candidate
                        break

        review: list = []
        text, stats = build_chapter(items, source_pages, pdf_path, image_dir, title, review,
                                    ocr_dir=folder)
        # 文件名：标题与 id 重复时只用 id，否则用 "id-标题"
        if nkey(title) == nkey(folder.name) or title == folder.name:
            filename = f'{folder.name}.md'
        else:
            filename = f'{folder.name}-{title}.md'
        (out_root / filename).write_text(text, encoding='utf-8')
        review_all[folder.name] = review
        report.append({'id': folder.name, 'file': filename, 'title': title, **stats})
        print(f'{folder.name}: {stats}', flush=True)

    if len(report) > 1:
        lines = ['# 目录', '']
        for entry in report:
            lines.append(f'- [{entry["title"]}]({entry["file"]})')
        (out_root / '00-目录.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    (out_root / 'build-report.json').write_text(
        json.dumps({'chapters': report, 'review': review_all}, ensure_ascii=False, indent=2),
        encoding='utf-8')
    total = {k: sum(r.get(k, 0) for r in report) for k in
             ('paragraphs', 'headings', 'equations', 'tables', 'figures',
              'text_corrected', 'text_review')}
    print()
    print('合计：', json.dumps(total, ensure_ascii=False))
    if total['text_review']:
        print(f'有 {total["text_review"]} 处文字差异待人工确认，见 build-report.json 的 review 字段')


if __name__ == '__main__':
    main()
