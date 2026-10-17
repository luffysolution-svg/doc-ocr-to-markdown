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
    构建状态保存在输出旁的 .<目录名>.build/；--agent-index 显式生成检索文件。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from console_output import safe_print as print
import tempfile
from collections import Counter, defaultdict
from contextlib import ExitStack
from urllib.parse import quote
from figure_identifiers import caption_keys, caption_identifier
from portable_paths import safe_path, logical_path, walk_files, read_text

from build_support import Publisher, asset_path, contained, load_book, read_items, relative_name, RESERVED, OWNERSHIP, digest
from difflib import SequenceMatcher
from pathlib import Path
from unicodedata import normalize

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_math import normalize_math, plain_text, split_leading_prose, process, normalize_heading

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
CAPTION = re.compile(r'^\s*(?:图|表|(?:Extended\s+Data\s+)?(?:Fig(?:ure)?\.?|Table))\s*[\d一二三四五六七八九十]', re.I)


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
    prs = Presentation(safe_path(path))
    sw, sh = prs.slide_width, prs.slide_height
    pages = []
    for slide in list(prs.slides):
        texts, pictures, unsafe = [], [], []
        for shape in slide.shapes:
            box = (shape.left / sw, shape.top / sh,
                   (shape.left + shape.width) / sw, (shape.top + shape.height) / sh)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and shape.width >= sw * 0.035 and shape.height >= sh * 0.025:
                transformed = (bool(shape.rotation) or any(abs(getattr(shape, key, 0)) > 1e-8
                               for key in ('crop_left', 'crop_top', 'crop_right', 'crop_bottom'))
                               or any(getattr(shape._element.spPr.xfrm, key, False) for key in ('flipH', 'flipV')))
                if transformed:
                    unsafe.append(box)
                else:
                    pictures.append({'box': box, 'ext': shape.image.ext, 'blob': shape.image.blob})
            elif shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                unsafe.append(box)
            elif shape.has_text_frame and shape.text.strip():
                unsafe.append(box)
                lines = [re.sub(r'[ \t]+', ' ', x).strip()
                         for x in shape.text_frame.text.split('\n')]
                text = flatten_paragraphs([x for x in lines if x])
                if not text or text in SKIP_TEXT:
                    continue
                texts.append({'box': box, 'text': text, 'key': nkey(text)})
            else:
                unsafe.append(box)
        pages.append({'texts': texts, 'pictures': pictures, 'unsafe_pictures': unsafe})
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
        score = min(overlap(box, pic['box']), overlap(pic['box'], box))
        if best is None or score > best[0]:
            best = (score, index, pic)
    if best and best[0] >= 0.98:
        return best
    return None


# ---------------------------------------------------------------- 图片落盘

class FigureStore:
    """图片按内容哈希命名，天然去重。"""

    def __init__(self, image_dir: Path):
        self.image_dir = image_dir
        self.count = 0

    def store(self, blob: bytes, ext: str) -> str | None:
        if Image is None:
            raise RuntimeError('需要 Pillow 校验图片')
        with Image.open(io.BytesIO(blob)) as im:
            im.load()
            if im.format in ('PNG', 'JPEG'):
                ext = 'jpg' if im.format == 'JPEG' else 'png'
            else:
                buffer = io.BytesIO()
                im.convert('RGBA').save(buffer, format='PNG')
                blob, ext = buffer.getvalue(), 'png'
        digest = hashlib.sha256(blob).hexdigest()
        name = f'{digest}.{ext}'
        self.image_dir.mkdir(parents=True, exist_ok=True)
        target = self.image_dir / name
        if not target.exists():
            target.write_bytes(blob)
        return name

    def native_pdf_image(self, page, box):
        """Extract only an exact, untransformed standalone image region.

        Any vector, text, second image, clipping ambiguity, mask or rotated image
        falls back to a rendered crop, which preserves the page composition.
        """
        try:
            import fitz
            region = fitz.Rect(box[0] * page.rect.width, box[1] * page.rect.height,
                               box[2] * page.rect.width, box[3] * page.rect.height)
            infos = [info for info in page.get_image_info(xrefs=True)
                     if fitz.Rect(info['bbox']).intersects(region)]
            if len(infos) != 1 or not infos[0].get('xref'):
                return None
            info = infos[0]
            if any(abs(a - b) > .5 for a, b in zip(info['bbox'], region)):
                return None
            a, b, c, d, _, _ = info['transform']
            if a <= 0 or d <= 0 or abs(b) > 1e-6 or abs(c) > 1e-6:
                return None
            marks = [(kind, bbox) for kind, bbox in page.get_bboxlog()
                     if fitz.Rect(bbox).intersects(region)]
            if len(marks) != 1 or marks[0][0] != 'fill-image':
                return None
            image = page.parent.extract_image(info['xref'])
            if not image or image.get('smask') or image.get('colorspace') not in (1, 3):
                return None
            return image['image'], image['ext']
        except Exception:
            return None

    def crop(self, pdf_page, box: tuple, scale: int = 1920) -> bytes | None:
        """裁图失败交还调用方，由其尝试原 OCR 图。"""
        try:
            import fitz
            x0 = max(0.0, box[0]) * pdf_page.rect.width
            y0 = max(0.0, box[1]) * pdf_page.rect.height
            x1 = min(1.0, box[2]) * pdf_page.rect.width
            y1 = min(1.0, box[3]) * pdf_page.rect.height
            if x1 <= x0 or y1 <= y0:
                return None
            factor = max(3.0, scale / pdf_page.rect.width)
            pix = pdf_page.get_pixmap(matrix=fitz.Matrix(factor, factor),
                                      clip=fitz.Rect(x0, y0, x1, y1), alpha=False)
            return pix.tobytes('png')
        except Exception:
            return None


# ---------------------------------------------------------------- 主装配

def captions_of(items: list[dict]) -> list[str]:
    values = []
    for item in items:
        for field in ('image_caption', 'chart_caption', 'table_caption', 'image_footnote', 'chart_footnote'):
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


def table_markdown(html: str, store: FigureStore | None = None, ocr_dir: Path | None = None,
                   image_dir_name: str = '') -> str:
    """MinerU 的 table_body 是 HTML，转成 Markdown 表格，并安全提取嵌入在单元格内的图片（如二维码、图标）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError('需要 beautifulsoup4 转换表格') from exc
    # 保护单元格公式中的 < 和 >，避免被 HTML 解析器当作未闭合标签吞噬（如 $x << R_c$）
    safe_html = re.sub(r'(\$[^$]+\$)', lambda m: m.group(1).replace('<', '&lt;').replace('>', '&gt;'), html)
    soup = BeautifulSoup(safe_html, 'html.parser')
    table = soup.find('table')
    if not table:
        return ''

    # 提取并替换单元格中的 <img> 为标准 Markdown 图片引用
    if store is not None and ocr_dir is not None and image_dir_name:
        for img in table.find_all('img'):
            src = img.get('src') or ''
            src_path = asset_path(ocr_dir, src)
            if not src_path.is_file():
                raise ValueError(f'缺少表格内嵌图片: {src_path}')
            saved_name = store.store(src_path.read_bytes(), src_path.suffix.lstrip('.'))
            if saved_name:
                alt = re.sub(r'[\[\]\n|]', ' ', img.get('alt', '表内图'))
                img.replace_with(f'![{alt}](images/{image_dir_name}/{saved_name})')

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


def full_page_scan(item: dict) -> bool:
    x0, y0, x1, y1 = box_of(item)
    return x1 - x0 >= 0.95 and y1 - y0 >= 0.95


def figure_numbers(items: list[dict]) -> set[str]:
    """Caption identity uses the same namespace and kind grammar as auditing."""
    captions = []
    for item in items:
        for field in ('image_caption', 'chart_caption', 'table_caption'):
            value = item.get(field) or []
            captions.extend([value] if isinstance(value, str) else value)
    return set(caption_keys(captions))


def image_groups(items: list[dict]) -> dict[int, list[dict]]:
    """Merge overlapping fragments without changing OCR order or merging references.

    Identical asset paths/boxes remain independent references. A text/table/equation
    between two images is a hard boundary; a full-page scan is never a fragment.
    """
    groups = {}
    index = 0
    while index < len(items):
        item = items[index]
        if item.get('type') not in ('image', 'chart') or full_page_scan(item):
            index += 1
            continue
        group = [item]
        numbers = figure_numbers(group)
        cursor = index + 1
        while cursor < len(items):
            other = items[cursor]
            if other.get('type') not in ('image', 'chart') or full_page_scan(other):
                break
            if group[-1].get('_figure_boundary') or other.get('_figure_boundary'):
                break
            other_numbers = figure_numbers([other])
            if numbers and other_numbers and len(numbers | other_numbers) > 1:
                break
            box, ob = union_box([box_of(g) for g in group]), box_of(other)
            same_reference = any(
                (other.get('img_path') and other.get('img_path') == g.get('img_path')
                 and other.get('_ocr_dir') == g.get('_ocr_dir')) or ob == box_of(g)
                for g in group)
            if same_reference or max(overlap(ob, box), overlap(box, ob)) < 0.35:
                break
            group.append(other)
            numbers.update(other_numbers)
            cursor += 1
        groups[index] = group
        index = cursor
    return groups


SPECIAL_HEADINGS = {'本章基本要求', '拓展学习资源', '复习题', '习题', '附录'}
EXERCISE = re.compile(r'^(?:[（(](?:\d+|[一二三四五六七八九十]+)[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[.．、)）](?!\d))')
EXERCISE_ACTION = re.compile(
    r'^\s*(?:[（(]?\d+[)）.]?\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*)'
    r'(?:计算|求解|试述|简述|证明|分析|说明|求出|判断|指出|讨论|解释|已知|设|若|试问|写出|画出|回答|选择|填空|简答|如图)'
)
SECTION = re.compile(r'^(?:★\s*)?\d+(?:\.\d+)+\s+\S')


def _sort_page_items(items: list[dict]) -> list[dict]:
    """Sort page items by vertical geometric reading flow.

    Quantizes y0 into 25-point vertical bands (~2.5% of page); within the
    same band, preserves original relative sequence so adjacent image panels
    and inline captions remain clustered.
    """
    return [it for _, it in sorted(
        enumerate(items),
        key=lambda pair: (
            int(((pair[1].get('bbox') or [0, 0, 0, 0])[1]) / 25),
            pair[0]
        )
    )]


def heading_text(text: str) -> str:
    text = re.sub(r'^\\\*\s*|^[*＊]\s*', '★ ', text.strip())
    return plain_text(text).strip()


def _cn_or_arabic_to_int(s: str) -> int | None:
    if not s:
        return None
    if s.isdigit():
        return int(s)
    mapping = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    if s in mapping:
        return mapping[s]
    if len(s) == 2 and s[0] == '十' and s[1] in mapping:
        return 10 + mapping[s[1]]
    if len(s) == 2 and s[0] in mapping and s[1] == '十':
        return mapping[s[0]] * 10
    if len(s) == 3 and s[0] in mapping and s[1] == '十' and s[2] in mapping:
        return mapping[s[0]] * 10 + mapping[s[2]]
    return None


def _parse_chapter_prefix(s: str) -> tuple[int | None, str]:
    s = s.strip()
    m = re.match(r'^(?:第\s*([0-9一二三四五六七八九十百]+)\s*章|\s*([0-9]+))\s*[.、\s\-:]*\s*(.*)$', s)
    if m:
        num_str = m.group(1) or m.group(2)
        val = _cn_or_arabic_to_int(num_str)
        name = m.group(3).strip()
        return val, name
    return None, s


def is_chapter_title_match(heading: str, title: str) -> bool:
    """Check if a heading matches the chapter title, allowing for chapter prefix variations."""
    if nkey(heading) == nkey(title):
        return True
    n1, name1 = _parse_chapter_prefix(title)
    n2, name2 = _parse_chapter_prefix(heading)
    if n1 is not None and n1 == n2 and nkey(name1) and nkey(name1) == nkey(name2):
        return True
    return False


def chapter_title_parts(items: list[dict], title: str) -> set[int]:
    """Only suppress split chapter labels when they reproduce the supplied title."""
    candidates = [(index, item) for index, item in enumerate(items)
                  if item.get('type') == 'text' and item.get('text', '').strip()]
    for pos, (index, item) in enumerate(candidates[:6]):
        text = item['text'].strip()
        if not (re.fullmatch(r'第[一二三四五六七八九十百\d]+章', text) or re.fullmatch(r'\d+', text)):
            continue
        combined, matched = text, {index}
        if is_chapter_title_match(combined, title):
            return matched
        for other_index, other in candidates[pos + 1:pos + 3]:
            if other.get('page_idx', 0) != item.get('page_idx', 0):
                break
            value = other['text'].strip()
            if not other.get('text_level') or len(value) > 60 or '$' in value:
                break
            combined += value
            matched.add(other_index)
            if is_chapter_title_match(combined.rstrip('①②③④⑤⑥⑦⑧⑨⑩'), title):
                return matched
    return set()


def inline_math(text: str) -> str:
    """Use the shared context-aware normalizer, verified by test_math.py.

    It protects code and Markdown links and keeps explicit math delimiters.
    """
    return process(text)


def fenced_code(body: str) -> str:
    """Unwrap one complete outer fence, then fence without content collisions."""
    info = ''
    lines = body.splitlines(keepends=True)
    opening = re.fullmatch(r' {0,3}(`{3,}|~{3,})([^\r\n]*)(?:\r?\n)?', lines[0]) if lines else None
    if opening and (opening[1][0] != '`' or '`' not in opening[2]):
        marker = opening[1]
        closing = rf' {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*(?:\r?\n)?'
        for index, line in enumerate(lines[1:], 1):
            if re.fullmatch(closing, line):
                if index == len(lines) - 1:
                    from markdown_it import MarkdownIt
                    token = MarkdownIt('commonmark').parse(body)[0]
                    body, info = token.content, token.info
                break
    char = '~' if '`' in info else '`'
    longest = max((len(run) for run in re.findall(re.escape(char) + '+', body)), default=0)
    fence = char * max(3, longest + 1)
    if body and not body.endswith('\n'):
        body += '\n'
    return f'{fence}{info}\n{body}{fence}'


def build_chapter(items: list[dict], source_pages: list[dict] | None, pdf_path: Path | None,
                  image_dir: Path, title: str, review: list,
                  ocr_dir: Path | None = None, mode: str = 'slides',
                  page_offset: int = 0, keep_table_images: bool = False,
                  page_map: list | None = None, page_count: int | None = None,
                  reading_order: str = 'geometry') -> tuple[str, dict]:
    store = FigureStore(image_dir)
    stats = defaultdict(int)
    title = re.sub(r'\s+', ' ', title).strip()
    content = [f'# {title}']
    by_page = defaultdict(list)
    for item in items:
        pi = item.get('page_idx', 0)
        if type(pi) is not int or pi < 0:
            raise ValueError(f'OCR page_idx 越界: {pi!r}')
        by_page[pi].append(item)

    with ExitStack() as resources:
        pdf = None
        if pdf_path:
            import fitz
            pdf = resources.enter_context(fitz.open(safe_path(pdf_path)))
            if page_offset < 0 or any(pi + page_offset >= len(pdf) for pi in by_page):
                raise ValueError('OCR 页码超出原 PDF 范围')
        if page_count is not None:
            if type(page_count) is not int or page_count < 1 or any(pi >= page_count for pi in by_page):
                raise ValueError('章节页数或 OCR 页码无效')
            for pi in range(page_count):
                by_page[pi]
        return _render_pages(by_page, source_pages, pdf, image_dir, title, review,
                             ocr_dir, mode, page_offset, keep_table_images, store, content, stats,
                             page_map=page_map, reading_order=reading_order)


def _render_pages(by_page, source_pages, pdf, image_dir, title, review, ocr_dir,
                  mode, page_offset, keep_table_images, store, content, stats, page_map=None,
                  reading_order='geometry'):
    headings = set()
    last_heading = None
    in_exercises = False
    have_section = False
    previous_level = 1
    code_blocks = set()
    figure_headings = set()
    all_items = [item for pi in sorted(by_page) for item in (_sort_page_items(by_page[pi]) if mode == 'book' and reading_order == 'geometry' else by_page[pi])]
    title_parts = {id(all_items[i]) for i in chapter_title_parts(all_items, title)} if mode == 'book' else set()
    table_ranges = defaultdict(list)
    for table in all_items:
        if table.get('type') == 'table':
            page = table.get('_page', table.get('page_idx', 0) + page_offset + 1)
            caps = table.get('table_caption') or []
            caps = [caps] if isinstance(caps, str) else caps
            for key in caption_keys(caps):
                table_ranges[key].append(page)
    for key, pages in table_ranges.items():
        if len(set(pages)) > 1:
            review.append({'kind': 'continued_table_preserved', 'identifier': key,
                           'pages': sorted(set(pages)), 'page_range': [min(pages), max(pages)],
                           'reason': 'Repeated table identity across pages; no verified cell/schema merge evidence'})
    marginal_counts = Counter(nkey(item.get('text', '')) for item in all_items
                              if item.get('type') in ('header', 'footer'))
    for pi in sorted(by_page):
        page_items = _sort_page_items(by_page[pi]) if mode == 'book' and reading_order == 'geometry' else by_page[pi]
        actual_pi = pi + page_offset
        source = source_pages[pi] if source_pages and pi < len(source_pages) else None
        used_text: set[int] = set()
        # 图片占据的区域：源文件的图 + OCR 识别出的图块，文字落在里面就是图内标签
        picture_regions = [p['box'] for p in source['pictures']] if source else []
        picture_regions += [box_of(i) for i in page_items if i.get('type') in ('image', 'chart') and not full_page_scan(i)]
        figure_blocks: list[str] = []
        emit: list[str] = []
        groups = image_groups(page_items)
        grouped_members = {id(member) for group in groups.values() for member in group[1:]}

        for item_index, item in enumerate(page_items):
            kind = item.get('type')
            item_ocr_dir = Path(item['_ocr_dir']) if item.get('_ocr_dir') else ocr_dir
            if id(item) in title_parts:
                stats['duplicate_chapter_labels'] += 1
                continue
            if kind == 'page_number':
                continue
            if kind in ('header', 'footer', 'discarded'):
                marginal = str(item.get('text', '')).strip()
                running_title = bool(SECTION.match(heading_text(marginal))) or nkey(marginal) == nkey(title)
                pure_math = bool(re.fullmatch(r'\$[^$]+\$', marginal))
                substantive = not (running_title or pure_math) and (
                    len(marginal) >= 40 or bool(re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩]', marginal))
                    or bool(re.match(r'^(?:[*＊]|\[\d+\]|[（(]\d+[）)])\s*\S', marginal))
                    or (bool(marginal) and marginal_counts[nkey(marginal)] == 1
                        and bool(re.search(r'[。；!?！？]', marginal))))
                if not substantive:
                    continue
                review.append({'page': actual_pi + 1, 'kind': 'retained_marginal_text',
                               'ocr_type': kind, 'text': marginal})
                kind = 'page_footnote'

            # ---- 图片 ----
            if kind in ('image', 'chart'):
                # Validate paths even if a PDF crop would otherwise hide an escape.
                for member in groups.get(item_index, [item]):
                    root = Path(member['_ocr_dir']) if member.get('_ocr_dir') else ocr_dir
                    if root is not None and member.get('img_path'):
                        asset_path(root, str(member['img_path']))
                if id(item) in grouped_members:
                    continue
                group = groups.get(item_index, [item])
                if full_page_scan(item):
                    stats['full_page_scans_preserved'] += 1
                    review.append({'page': actual_pi + 1, 'kind': 'full_page_image_preserved',
                                   'bbox': item.get('bbox')})
                box = union_box([box_of(member) for member in group])
                caps = captions_of(group)
                names = []
                recrop_required = any(member.get('_recrop_required') or member.get('_added_image')
                                      for member in group)
                if recrop_required and pdf is None:
                    raise ValueError(f'修复图片必须从原 PDF 重裁: page={actual_pi + 1}')
                unsafe_native = source and any(max(overlap(box, region), overlap(region, box)) > 0
                                               for region in source.get('unsafe_pictures', []))
                if source and sum(max(overlap(box, picture['box']), overlap(picture['box'], box)) > 0
                                  for picture in source['pictures']) > 1:
                    unsafe_native = True
                match = best_picture_match(box, source['pictures'], set()) if source and not recrop_required and not unsafe_native else None
                if match and overlap(box, match[2]['box']) >= 0.9:
                    names.append(store.store(match[2]['blob'], match[2]['ext']))
                    stats['native_figures'] += 1
                if not names and pdf is not None and not recrop_required:
                    native = store.native_pdf_image(pdf[actual_pi], box)
                    if native:
                        names.append(store.store(*native))
                        stats['native_pdf_figures'] += 1
                if not names and pdf is not None:
                    blob = store.crop(pdf[actual_pi], box)
                    if blob:
                        names.append(store.store(blob, 'png'))
                        stats['cropped_figures'] += 1
                if recrop_required and not names:
                    raise ValueError(f'修复图片 PDF 重裁失败: page={actual_pi + 1}')
                if not names and unsafe_native:
                    raise ValueError(f'PPTX transformed/composed region requires a successful PDF crop: page={actual_pi + 1}')
                if not names:
                    # A failed union crop falls back to every fragment, preserving data.
                    for member in group:
                        root = Path(member['_ocr_dir']) if member.get('_ocr_dir') else ocr_dir
                        if root is None or not member.get('img_path'):
                            raise ValueError(f'图片裁切失败且缺少 OCR 回退图: page={actual_pi + 1}')
                        candidate = asset_path(root, str(member['img_path']))
                        if not candidate.is_file():
                            raise ValueError(f'缺少 OCR 回退图: {candidate}')
                        names.append(store.store(candidate.read_bytes(), candidate.suffix.lstrip('.')))
                        stats['ocr_figures'] += 1
                names = list(dict.fromkeys(name for name in names if name))
                store.count += len(names)
                alt = plain_text(caps[0]) if caps else title
                alt = re.sub(r'[\[\]\n|]', ' ', alt).strip()
                target_container = emit if mode == 'book' else figure_blocks
                for name in names:
                    target_container.append(f'![{alt}](images/{image_dir.name}/{name})')
                for cap in caps:
                    identifier = caption_identifier(cap)
                    if identifier and identifier.namespace == 'supplemental' and identifier.heading not in figure_headings:
                        target_container.append('## ' + identifier.heading)
                        figure_headings.add(identifier.heading)
                    target_container.append(inline_math(cap))
                stats['figures'] += 1
                continue

            # ---- 正文 ----
            if kind == 'text':
                raw = item.get('text', '')
                text = re.sub(r'[ \t]+', ' ', raw).strip()
                if not text or text in SKIP_TEXT or LABEL_ONLY.match(text):
                    continue
                # 过滤 MinerU 模型推理说明泄漏
                if re.search(r'The Ground Truth|According to Rule|provided OCR content|OCR result must', text):
                    continue
                # 画在图片上的标签：图片文件里已包含，不重复成正文
                if mode == 'slides' and picture_regions and len(text) <= 12:
                    item_box = box_of(item)
                    if any(overlap(item_box, region) > 0.6 for region in picture_regions):
                        stats['embedded_labels'] += 1
                        continue

                # 标题判断逻辑：book 模式遵循严格章节与节号规则，避免页顶正文误升标题
                if mode == 'book':
                    disp = heading_text(text)
                    is_chapter_title = bool(re.match(r'^第[一二三四五六七八九十百\d]+章(?:\s+[^。！？?!]{1,60})?$', disp))
                    is_special_heading = disp in SPECIAL_HEADINGS
                    if disp in ('复习题', '习题'):
                        in_exercises = True
                    elif is_chapter_title or disp == '附录':
                        in_exercises = False
                    sec_match = SECTION.match(disp) if not in_exercises and not re.search(r'[。！？?!]', text) else None
                    is_action_exercise = bool(EXERCISE_ACTION.search(text))
                    trusted_title = (bool(item.get('text_level')) and len(text) < 80
                                     and not re.search(r'[。？！?！:：]', text) and not in_exercises
                                     and not is_action_exercise)
                    exercise = (bool(EXERCISE.match(text)) and not trusted_title) or (in_exercises and bool(re.match(r'^\d+\.\d+\b', text)))
                    is_heading = not exercise and (trusted_title or (sec_match and len(text) < 65)
                                                   or is_chapter_title or is_special_heading)
                else:
                    is_heading = bool(item.get('text_level')) or (box_of(item)[1] < 0.152 and len(text) < 100 and '$' not in text)

                if is_heading:
                    if is_chapter_title_match(text, title):
                        continue
                    identity = nkey(text)
                    if identity == last_heading and identity in headings:
                        continue
                    level = 2
                    if mode == 'book' and have_section and not (sec_match or is_special_heading or is_chapter_title):
                        level = 3
                    if mode == 'book' and sec_match:
                        have_section = True
                    m = re.match(r'^(\d+(?:\.\d+)*)', text)
                    if m and not (mode == 'book' and not sec_match):
                        level = max(2, min(5, len(m.group(1).split('.'))))
                    elif re.match(r'^第[一二三四五六七八九十百\d]+章', text):
                        level = 2
                    disp = normalize_heading(text)
                    level = min(level, previous_level + 1)
                    previous_level = level
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

                # OCR cannot reliably distinguish page continuation from a new paragraph.
                emit.append(inline_math(text))
                last_heading = None
                stats['paragraphs'] += 1
                continue

            # ---- 公式 ----
            if kind == 'equation':
                body = normalize_math(item.get('text', ''))
                if not body:
                    continue
                leading, body, trailing = split_leading_prose(body)
                if leading:
                    emit.append(leading)
                    stats['equation_captions'] += 1
                body = body.strip()
                if not body:
                    continue
                if body.startswith('$$'):
                    body = body[2:-2].strip()
                emit.append(f'$$\n{body}\n$$')
                if trailing:
                    emit.append(trailing)
                last_heading = None
                stats['equations'] += 1
                continue

            # ---- 表格 ----
            if kind == 'table':
                for cap in captions_of([item]):
                    identifier = caption_identifier(cap)
                    if identifier and identifier.namespace == 'supplemental' and identifier.heading not in figure_headings:
                        emit.append('## ' + identifier.heading)
                        figure_headings.add(identifier.heading)
                    emit.append(inline_math(cap))
                md = table_markdown(item.get('table_body', ''), store=store, ocr_dir=item_ocr_dir,
                                    image_dir_name=image_dir.name)
                if md:
                    emit.append(inline_math(md))
                    stats['tables'] += 1
                if keep_table_images and pdf is not None and actual_pi < len(pdf):
                    blob = store.crop(pdf[actual_pi], box_of(item))
                    if blob:
                        t_name = store.store(blob, 'png')
                        if t_name:
                            emit.append(f'![数据表原图](images/{image_dir.name}/{t_name})')
                            stats['table_images'] += 1
                notes = item.get('table_footnote') or []
                for note in ([notes] if isinstance(notes, str) else notes):
                    if isinstance(note, str) and note.strip():
                        emit.append(inline_math(note.strip()))
                continue

            # ---- 列表 / 代码 ----
            if kind == 'list':
                for entry in item.get('list_items', []):
                    if str(entry).strip():
                        emit.append('- ' + inline_math(str(entry).strip()))
                continue
            if kind in ('code', 'algorithm'):
                body = item.get('code_body') or item.get('text', '')
                if body:
                    code = fenced_code(body)
                    code_blocks.add(code)
                    emit.append(code)
                continue
            if kind == 'ref_text':
                raw_ref = item.get('text', '')
                text = re.sub(r'[ \t]+', ' ', str(raw_ref)).strip()
                if text and not LABEL_ONLY.match(text):
                    emit.append(inline_math(text))
                    stats['paragraphs'] += 1
                continue
            if item.get('text'):
                text = re.sub(r'[ \t]+', ' ', str(item['text'])).strip()
                if text and not LABEL_ONLY.match(text):
                    emit.append(inline_math(text))

        page_start = len('\n\n'.join(content).splitlines()) + 2 if content else 1
        if mode == 'slides':
            content.extend(figure_blocks)
        content.extend(emit)
        if page_map is not None:
            output_blocks = figure_blocks + emit if mode == 'slides' else emit
            page_end = len('\n\n'.join(content).splitlines())
            page_map.append({'pdf_page': actual_pi + 1, 'start_line': page_start if output_blocks else None,
                             'end_line': page_end if output_blocks else None,
                             'status': 'included' if output_blocks else 'no_output',
                             'ocr_blocks': len(page_items)})

    if page_map is None:
        content = [b if b in code_blocks else re.sub(r'\n{3,}', '\n\n', b) for b in content]
    text = '\n\n'.join(b for b in content if b and b.strip())
    stats['figures'] = store.count
    return text.strip() + '\n', dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description='把 OCR 结果装配成 Markdown 知识库')
    parser.add_argument('--raw', required=True, help='OCR 结果目录（含各文件的 content_list.json）')
    parser.add_argument('--out', required=True, help='输出目录')
    parser.add_argument('--mode', choices=['slides', 'book'], default='slides',
                        help='slides 图片置顶；book 自然图文顺序、严格标题；保守保留跨页段落边界')
    parser.add_argument('--reading-order', choices=['geometry', 'source'], default='geometry',
                        help='geometry 按高度排序；source 保留经核对的 OCR 阅读顺序（双栏论文）')
    parser.add_argument('--metadata-json', help='JSON：章节 id 到已核对元数据的映射，发布前写入 YAML frontmatter')
    parser.add_argument('--clean', action='store_true', help='已禁用：拒绝目录清理')
    parser.add_argument('--prune-orphans', action='store_true', help='清理交付目录中历史版本遗留但当前未引用的切图文件')
    parser.add_argument('--overwrite', action='store_true', help='显式覆盖所有权清单中已被修改的生成文件，绝不覆盖未登记文件')
    parser.add_argument('--manifest', help='分片清单 JSON 路径（支持多批块大文档组装与全局页码偏移计算）')
    parser.add_argument('--chapters', help='章节划分 JSON 路径（按起止页重组为规范教材章节）')
    parser.add_argument('--pdf', help='整本原 PDF 文件路径（用于高清裁切原图与数据表原图）')
    parser.add_argument('--pdf-dir', help='PDF 目录（源文件里没有的图从这里裁切）')
    parser.add_argument('--keep-table-images', action='store_true', help='为复杂数据表额外保留原扫描图高清裁片，实现文字检索与原版验算双轨保真')
    parser.add_argument('--repairs', help='回看原页后确认的修复清单 JSON；旧值或定位不符则停止')
    parser.add_argument('--auto-repairs', action='store_true', help='未提供 --repairs 时自动推导并应用高置信度多面板图块缝合')
    parser.add_argument('--agent-index', action='store_true', help='显式生成 Agent/RAG 分块、页码映射与检索清单')
    parser.add_argument('--state-dir', help='外部构建状态目录，默认是输出目录旁的 .<目录名>.build')
    parser.add_argument('--source-dir', help='源文件目录（用于文字校正与内嵌原图，如 PPTX）')
    parser.add_argument('--source-glob', default='*.pptx')
    parser.add_argument('--only', nargs='*', help='只处理这些子目录（data_id）')
    parser.add_argument('--titles', help='JSON 文件：{"data_id": "章节标题"}')
    args = parser.parse_args()

    if args.clean:
        parser.error('--clean 已禁用：不会清理任何目录')
    if bool(args.manifest) != bool(args.chapters):
        parser.error('--manifest 与 --chapters 必须一起提供')
    raw_root = logical_path(safe_path(args.raw).resolve())
    # Preserve the supplied final component so Publisher can reject output symlinks.
    out_root = Path(args.out).absolute()
    if not safe_path(raw_root).is_dir():
        parser.error(f'OCR 目录不存在: {raw_root}')
    if out_root.resolve().is_relative_to(raw_root) or raw_root.is_relative_to(out_root.resolve()):
        parser.error('输入和输出目录不得相互包含')
    publisher = Publisher(out_root, args.overwrite, Path(args.state_dir).absolute() if args.state_dir else None)
    if raw_root.is_relative_to(publisher.state_root.resolve()) or publisher.state_root.resolve().is_relative_to(raw_root):
        parser.error('构建状态与 OCR 输入目录不得相互包含')
    previous = publisher.previous_report()
    if not args.agent_index and contained(out_root, 'agent/manifest.json').exists():
        parser.error('已有 Agent 索引；重建时显式添加 --agent-index 更新，或使用新的干净输出目录')
    only = set(args.only) if args.only is not None else None
    if only == set():
        parser.error('--only 至少需要一个 id')
    sources = {}
    if args.source_dir:
        for path in sorted((p for p in walk_files(Path(args.source_dir), args.source_glob) if p.parent == Path(args.source_dir).absolute())):
            stem = re.sub(r'^\d+[\s\-_]*', '', path.stem).strip()
            sources[path.stem] = path
            sources[stem] = path
    jobs = []
    mode = 'book' if args.manifest else args.mode
    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        manifest = json.loads(read_text(manifest_path))
        sections = json.loads(read_text(Path(args.chapters)))
        pdf_path = Path(args.pdf).resolve() if args.pdf else None
        if pdf_path is None and manifest.get('source'):
            source = Path(manifest['source'])
            pdf_path = source if source.is_absolute() else manifest_path.parent / source
        blocks = load_book(raw_root, manifest, sections, pdf_path)
        repairs_applied = []
        if args.repairs:
            from book_features import apply_repairs
            blocks, repairs_applied = apply_repairs(
                blocks, json.loads(read_text(Path(args.repairs))), pdf_path)
        elif args.auto_repairs and pdf_path is not None:
            from audit_figures import generate_confident_repairs_for_blocks
            from book_features import apply_repairs
            auto_reps = generate_confident_repairs_for_blocks(blocks)
            if auto_reps:
                blocks, repairs_applied = apply_repairs(blocks, auto_reps, pdf_path)
        identities = {sec['id'] for sec in sections}
        if only and not only <= identities:
            raise ValueError(f'--only 未知章节: {sorted(only - identities)}')
        for sec in sorted(sections, key=lambda sec: sec['start']):
            if only is not None and sec['id'] not in only:
                continue
            items = [{**b, 'page_idx': b['_page'] - sec['start']}
                     for b in blocks if sec['start'] <= b['_page'] <= sec['end']]
            jobs.append({'id': sec['id'], 'title': sec['title'],
                         'file': sec.get('file', f"{sec['id']}.md"),
                         'items': items, 'pdf': pdf_path, 'offset': sec['start'] - 1,
                         'source': None, 'ocr_dir': None, 'page_count': sec['end'] - sec['start'] + 1})
    else:
        if args.repairs:
            parser.error('--repairs 需要 --manifest 与 --chapters，以确定全书页码')
        repairs_applied = []
        titles = json.loads(read_text(Path(args.titles))) if args.titles else {}
        folders = sorted(d for d in safe_path(raw_root).iterdir() if d.is_dir())
        identities = {d.name for d in folders}
        if only and not only <= identities:
            raise ValueError(f'--only 未知目录: {sorted(only - identities)}')
        for ordinal, folder in enumerate(folders):
            if only is not None and folder.name not in only:
                continue
            relative_name(folder.name, component=True)
            folder = contained(raw_root, folder.name)
            items, ocr_dir = read_items(folder)
            title = titles.get(folder.name, folder.name)
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f'无效标题: {folder.name}')
            source_path = match_source(folder.name, sources, ordinal) if sources else None
            pdf_path = Path(args.pdf).resolve() if args.pdf else None
            if pdf_path is None and args.pdf_dir:
                candidates = sorted(walk_files(Path(args.pdf_dir), '*.pdf'))
                pdf_path = next((p for p in candidates if
                                 (source_path and p.stem == source_path.stem) or folder.name in p.stem), None)
                if pdf_path is None:
                    number = leading_number(folder.name)
                    pdf_path = next((p for p in candidates if number is not None and leading_number(p.stem) == number), None)
            # IDs are stable output names; titles are content, not filesystem paths.
            jobs.append({'id': folder.name, 'title': title, 'file': f'{folder.name}.md',
                         'items': items, 'pdf': pdf_path, 'offset': 0,
                         'source': load_source(source_path) if source_path else None,
                         'ocr_dir': ocr_dir, 'page_count': max((i.get('page_idx', 0) for i in items), default=0) + 1})
    if not jobs:
        raise ValueError('没有找到可装配的 OCR 结果')
    if previous.get('chapters') and previous.get('mode') != mode:
        raise ValueError('已有输出模式不同，请使用新输出目录')
    metadata = json.loads(read_text(Path(args.metadata_json))) if args.metadata_json else {}
    if not isinstance(metadata, dict) or any(not isinstance(value, dict) for value in metadata.values()):
        raise ValueError('metadata-json 必须是章节 id 到元数据对象的映射')
    if metadata.keys() - identities:
        raise ValueError('metadata-json 含未知章节 id')
    files_seen = set()
    for job in jobs:
        filename = relative_name(job['file'])
        if filename.split('/')[0].casefold() in {name.casefold() for name in RESERVED} or Path(filename).suffix.lower() != '.md':
            raise ValueError(f'章节文件名保留或无效: {filename}')
        if filename.casefold() in files_seen:
            raise ValueError(f'重复输出文件: {filename}')
        files_seen.add(filename.casefold())
    # Build outside the output tree; failures leave all existing output untouched.
    with tempfile.TemporaryDirectory(prefix='markdown-build-') as temporary:
        stage = Path(temporary)
        updated, review_all = [], dict(previous.get('review', {}))
        page_maps = dict(previous.get('page_maps', {}))
        for job in jobs:
            image_dir = stage / 'images' / job['id']
            review = []
            page_map = []
            text, stats = build_chapter(
                job['items'], job['source'], job['pdf'], image_dir, job['title'], review,
                ocr_dir=job['ocr_dir'], mode=mode, page_offset=job['offset'],
                keep_table_images=args.keep_table_images, page_map=page_map,
                page_count=job['page_count'], reading_order=args.reading_order)
            if job['id'] in metadata:
                from research_metadata import render_frontmatter, prepare_research_markdown
                original_lines = len(text.splitlines())
                text = prepare_research_markdown(text, metadata[job['id']])
                banner_lines = 3 if metadata[job['id']].get('supplement_to') else 0
                for mapping in page_map:
                    for key in ('start_line', 'end_line'):
                        if mapping.get(key) is not None:
                            mapping[key] += banner_lines
                if page_map and metadata[job['id']].get('has_supplement'):
                    last = next((entry for entry in reversed(page_map) if entry.get('end_line') is not None), None)
                    if last:
                        last['end_line'] += len(text.splitlines()) - original_lines - banner_lines
                header = render_frontmatter(metadata[job['id']])
                text = header + text
                for mapping in page_map:
                    for key in ('start_line', 'end_line'):
                        if mapping.get(key) is not None:
                            mapping[key] += header.count('\n')
            page_maps[job['id']] = page_map
            # Images live at output root/images even for nested chapter paths.
            prefix = '../' * (len(Path(job['file']).parts) - 1)
            text = text.replace('](images/', '](' + prefix + 'images/')
            target = contained(stage, job['file'])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8', newline='\n')
            updated.append({'id': job['id'], 'file': job['file'], 'title': job['title'], **stats})
            review_all[job['id']] = review
        replacements = {entry['id']: entry for entry in updated}
        report = [replacements.pop(entry['id'], entry) for entry in previous.get('chapters', [])]
        report.extend(replacements.values())
        if args.manifest:
            order = {sec['id']: i for i, sec in enumerate(sorted(sections, key=lambda s: s['start']))}
            report.sort(key=lambda e: (order.get(e['id'], len(order)), e['id']))
        else:
            report.sort(key=lambda e: e['id'])
        if len({e['file'].casefold() for e in report}) != len(report):
            raise ValueError('输出文件与保留章节冲突')
        lines = ['# 目录', '']
        for entry in report:
            label = re.sub(r'[\[\]\n]', ' ', entry['title'])
            lines.append(f'- [{label}]({quote(entry["file"], safe="/")})')
        (stage / '00-目录.md').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
        if args.agent_index:
            from book_features import write_agent_index
            for entry in report:
                retained = contained(stage, entry['file'])
                if not retained.exists():
                    existing = contained(out_root, entry['file'])
                    retained.parent.mkdir(parents=True, exist_ok=True)
                    retained.write_bytes(existing.read_bytes())
                if entry['id'] not in page_maps:
                    raise ValueError('旧版本缺少页码映射，请完整重建后生成 Agent 索引')
            source_ref = None
            if args.manifest and pdf_path:
                import os
                source_ref = Path(os.path.relpath(pdf_path, out_root)).as_posix()
            write_agent_index(stage, report, page_maps, source_pdf=source_ref)
        retained_repairs = []
        if only is not None:
            replaced_pages = {entry['pdf_page'] for job in jobs for entry in page_maps[job['id']]}
            retained_repairs = [repair for repair in previous.get('repairs', [])
                                if repair.get('page') not in replaced_pages]
        (stage / 'build-report.json').write_text(json.dumps(
            {'chapters': report, 'review': review_all, 'mode': mode, 'page_maps': page_maps,
             'repairs': retained_repairs + repairs_applied,
             'paragraph_joining': 'disabled: conservative preservation of OCR paragraph/page boundaries'},
            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        publisher.publish(stage)
        if args.prune_orphans:
            _prune_orphan_images(out_root, publisher.state_root)
    print(f'装配完成：更新 {len(updated)} 章，目录保留 {len(report)} 章')


def _prune_orphan_images(out_root: Path, state_root: Path) -> int:
    """Safely remove unreferenced image assets owned by the builder."""
    ledger_path = contained(state_root, OWNERSHIP)
    if not ledger_path.exists():
        ledger_path = contained(out_root, OWNERSHIP)
    if not ledger_path.exists():
        return 0
    try:
        data = json.loads(ledger_path.read_text(encoding='utf-8'))
        owned = data.get('files', {})
    except Exception:
        return 0

    from markdown_resources import parse_resources, resolve_resource
    referenced = set()
    try:
        for md_file in walk_files(out_root, '*.md'):
            content = read_text(md_file)
            resources = parse_resources(content)
            # Unresolved reference syntax is plain text to CommonMark. Keep all
            # assets if image-like markup could not be accounted for by parsing.
            if len(re.findall(r'!\[|<img', content, re.I)) > sum(r.kind == 'image' for r in resources):
                return 0
            for resource in resources:
                # Links also preserve assets: a user can link an image without
                # embedding it. An unresolved reference makes pruning unsafe.
                resolved = resolve_resource(resource, md_file, out_root)
                if resolved is not None:
                    resolved = resolved.resolve()
                    if resolved.is_relative_to(out_root.resolve()):
                        referenced.add(resolved.relative_to(out_root.resolve()).as_posix())
    except (OSError, ValueError, TypeError, KeyError):
        return 0

    pruned = 0
    updated_owned = dict(owned)
    for rel_path, expected_digest in list(owned.items()):
        if not rel_path.startswith('images/'):
            continue
        if rel_path not in referenced:
            actual_file = contained(out_root, rel_path)
            if actual_file.is_file():
                try:
                    if digest(actual_file.read_bytes()) == expected_digest:
                        actual_file.unlink()
                        del updated_owned[rel_path]
                        pruned += 1
                except Exception:
                    pass

    if pruned > 0:
        ledger_data = json.dumps({'version': 1, 'files': updated_owned},
                                 ensure_ascii=False, indent=2) + '\n'
        ledger_path.write_text(ledger_data, encoding='utf-8')
        print(f'已清理 {pruned} 个未引用的旧切图文件')
    return pruned


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f'装配失败: {exc}') from exc
