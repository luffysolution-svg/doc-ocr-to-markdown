#!/usr/bin/env python
"""从 Markdown 知识库中抽取全部公式，输出给 validate_math.cjs 校验。

用法：
    python extract_formulas.py <目录> --out formulas.json
    python extract_formulas.py <目录>            # 打印统计

输出格式：[{"file": "...", "tex": "...", "line": 12}, ...]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from console_output import safe_print as print

from normalize_math import scan_math, math_issues


from portable_paths import safe_path as _safe_path, walk_files, read_text, write_text


def extract(root: Path) -> list[dict]:
    records = []
    root = root.absolute()
    for path in walk_files(root, '*.md'):
        text = read_text(path)
        spans, issues = scan_math(text)
        filename = path.relative_to(root).as_posix()
        for span in spans:
            records.append({
                'file': filename,
                'tex': span.body,
                'raw': span.group(),
                'display': span.display,
                'line': text.count('\n', 0, span.start()) + 1,
                'errors': math_issues(span.body),
            })
        for issue in issues:
            records.append({'file': filename, 'tex': issue['tex'], 'line': issue['line'],
                            'kind': 'parse-error', 'errors': [issue['error']]})
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description='抽取 Markdown 里的公式')
    parser.add_argument('root')
    parser.add_argument('--out', help='输出 JSON 路径')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not _safe_path(root).is_dir():
        raise SystemExit(f'目录不存在：{root}')

    records = extract(root)
    valid = [r for r in records if r.get('kind') != 'parse-error']
    display = sum(bool(r['display']) for r in valid)
    parse_errors = len(records) - len(valid)
    print(f'共 {len(valid)} 个公式（独立 {display}，行内 {len(valid) - display}），定界错误 {parse_errors}')

    if args.out:
        target = _safe_path(Path(args.out))
        target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已写入 {args.out}')
    if any(record.get('errors') for record in records):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
