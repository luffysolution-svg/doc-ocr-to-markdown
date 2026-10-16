#!/usr/bin/env python
"""交付前校验：检查 Markdown 知识库的结构完整性和资源引用。

用法：
    python validate_output.py <知识库目录> [--images-dir images]

检查项：
    - 每个文件只有一个一级标题，标题层级不跳跃
    - 标题里没有 LaTeX 残留
    - 图片引用数与实际文件数一致、无重复引用、无失效链接
    - 无残留编辑痕迹（页码、"来源"、"第 N 页"、工作目录路径等）
    - 所有图片可解码
    - 公式定界符配对，括号配平

退出码 0 表示通过，1 表示有错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from console_output import safe_print as print
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from markdown_it import MarkdownIt
except ImportError:
    MarkdownIt = None

try:
    from PIL import Image
except ImportError:
    Image = None

from normalize_math import MATH_SPAN, paren_issues, scan_math, protected_markdown

# 不该出现在交付内容里的编辑痕迹
LEAK_PATTERNS = [
    (r'<details\b', 'HTML details 折叠块'),
    (r'<summary\b', 'HTML summary 标签'),
    (r'^\s*#{1,6}\s*第\s*\d+\s*页', '页标题残留'),
    (r'查看第\s*\d+\s*页', '原图链接残留'),
    (r'来源[：:]\s*本', '"来源：" 标注残留'),
    (r'\.ocr[-_]work', '临时工作目录路径'),
    (r'疑似(笔误|错误)', '编辑注释残留'),
    (r'\ufffd', '编码替换字符'),
    (r'The Ground Truth\b', '模型推理/评测说明泄漏'),
    (r'According to Rule\b', '模型系统提示词泄漏'),
    (r'provided OCR content\b', '模型提示词说明泄漏'),
]
# 只报警不报错
WARN_PATTERNS = [
    (r'^\s*#{1,6}\s*.*\bTODO\b', 'TODO 标记'),
    (r'^\s*#{1,6}\s*.*xx+', '占位符'),
]


def check_file(path: Path, images_dir: Path | None, image_hashes: Counter) -> tuple[list, list, dict]:
    errors: list[str] = []
    warnings: list[str] = []
    text = path.read_text(encoding='utf-8')
    name = path.name

    for pattern, label in LEAK_PATTERNS:
        if re.search(pattern, text, re.M):
            errors.append(f'{name}: 存在{label}')
    for pattern, label in WARN_PATTERNS:
        if re.search(pattern, text, re.M):
            warnings.append(f'{name}: 存在{label}')

    # Ignore headings shown as code examples.
    visible_text = protected_markdown(text)
    headings = re.findall(r'^(#{1,6})\s+(.*)$', visible_text, re.M)
    if sum(1 for h, _ in headings if h == '#') != 1:
        errors.append(f'{name}: 一级标题数量为 {sum(1 for h,_ in headings if h=="#")}，应为 1')
    levels = [len(h) for h, _ in headings]
    for previous, current in zip(levels, levels[1:]):
        if current - previous > 1:
            errors.append(f'{name}: 标题层级跳跃 {previous}→{current}')
    for _, title in headings:
        if '\\' in title or '$' in title:
            errors.append(f'{name}: 标题含公式残留「{title[:40]}」')

    # 公式
    math_spans, syntax_issues = scan_math(text)
    for issue in syntax_issues:
        errors.append(f'{name}:{issue["line"]}: {issue["error"]}')
    for match in math_spans:
        body = match.body.strip()
        if not body:
            errors.append(f'{name}: 空公式')
        elif body.startswith('$') or body.endswith('$'):
            errors.append(f'{name}: 公式定界符异常「{body[:40]}」')
    for issue in paren_issues(text):
        warnings.append(f'{name}: 括号不配平「{issue}」')

    # 图片引用与链接
    images = tables = 0
    if MarkdownIt is not None:
        parser = MarkdownIt('commonmark', {'html': True}).enable('table')
        for token in parser.parse(text):
            if token.type == 'table_open':
                tables += 1
            for child in token.children or []:
                if child.type not in ('image', 'link_open'):
                    continue
                dest = child.attrGet('src' if child.type == 'image' else 'href')
                if not dest:
                    continue
                parts = urlsplit(dest)
                if parts.scheme or parts.netloc:
                    if child.type == 'image':
                        errors.append(f'{name}: 图片使用绝对/远程地址 {dest}')
                    continue
                decoded = unquote(parts.path).replace('\\', '/')
                target = (path.parent / decoded).resolve()
                if child.type == 'image':
                    asset_root = images_dir.parent.resolve() if images_dir else path.parent.resolve()
                    if decoded.startswith('/') or not target.is_relative_to(asset_root):
                        errors.append(f'{name}: 图片路径越出知识库 {dest}')
                        continue
                if not target.exists():
                    errors.append(f'{name}: 链接失效 {dest}')
                    continue
                if child.type == 'image':
                    images += 1
                    asset_root = images_dir.parent.resolve() if images_dir else path.parent.resolve()
                    image_hashes[target.relative_to(asset_root).as_posix()] += 1
                    alt = child.attrGet('alt') or ''
                    if any(c in alt for c in ('$', '\\', '{', '}')):
                        errors.append(f'{name}: 图片说明含公式符号（会破坏 image 标签）「{alt[:40]}」')
                    if Image is not None:
                        try:
                            with Image.open(target) as im:
                                im.verify()
                        except Exception as exc:
                            errors.append(f'{name}: 图片损坏 {dest}: {exc}')
    else:
        for match in re.finditer(r'!\[(.*?)\]\((.*?)\)', text):
            images += 1
            target = (path.parent / unquote(match.group(2))).resolve()
            if not target.exists():
                errors.append(f'{name}: 链接失效 {match.group(2)}')

    return errors, warnings, {'file': name, 'characters': len(text), 'images': images,
                              'tables': tables, 'math': len(math_spans)}


def validate(root: Path, images_dir: Path | None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    documents = []
    image_hashes: Counter = Counter()

    markdown_files = sorted(root.rglob('*.md'))
    if not markdown_files:
        errors.append(f'{root} 下没有 Markdown 文件')
    for path in markdown_files:
        file_errors, file_warnings, summary = check_file(path, images_dir, image_hashes)
        errors.extend(file_errors)
        warnings.extend(file_warnings)
        documents.append(summary)

    duplicates = [name for name, count in image_hashes.items() if count > 1]

    if images_dir and images_dir.exists():
        orphans = [f for f in images_dir.rglob('*.*')
                   if f.is_file() and str(f.relative_to(root)).replace('\\', '/') not in image_hashes]
        if orphans:
            warnings.append(f'{len(orphans)} 张图片未被任何文档引用')

    from book_features import validate_agent_index
    errors.extend(validate_agent_index(root))

    report = {
        'status': 'pass' if not errors else 'fail',
        'root': str(root),
        'documents': len(documents),
        'total_images': sum(d['images'] for d in documents),
        'total_math': sum(d['math'] for d in documents),
        'total_characters': sum(d['characters'] for d in documents),
        'duplicate_images': len(duplicates),
        'errors': errors,
        'warnings': warnings,
        'detail': documents,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='知识库 Markdown 交付前校验')
    parser.add_argument('root', help='知识库根目录')
    parser.add_argument('--images-dir', default=None, help='图片目录（相对 root），用于检查孤立图片')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        sys.exit(f'目录不存在：{root}')
    images_dir = (root / args.images_dir) if args.images_dir else None

    report = validate(root, images_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f'状态：{report["status"]}')
        print(f'文档 {report["documents"]} 篇，图片 {report["total_images"]} 张，'
              f'公式 {report["total_math"]} 个，共 {report["total_characters"]} 字符')
        if report['errors']:
            print(f'\n错误 {len(report["errors"])} 项：')
            for item in report['errors'][:40]:
                print(f'  ✗ {item}')
        if report['warnings']:
            print(f'\n警告 {len(report["warnings"])} 项：')
            for item in report['warnings'][:20]:
                print(f'  ! {item}')
        if report['status'] == 'pass':
            print('\n全部检查通过。')

    raise SystemExit(0 if report['status'] == 'pass' else 1)


if __name__ == '__main__':
    main()
