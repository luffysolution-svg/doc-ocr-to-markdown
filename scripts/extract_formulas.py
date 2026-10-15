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

from normalize_math import scan_math, math_issues


def extract(root: Path) -> list[dict]:
    records = []
    for path in sorted(root.rglob('*.md')):
        text = path.read_text(encoding='utf-8')
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
    if not root.is_dir():
        raise SystemExit(f'目录不存在：{root}')

    records = extract(root)
    valid = [r for r in records if r.get('kind') != 'parse-error']
    display = sum(bool(r['display']) for r in valid)
    parse_errors = len(records) - len(valid)
    print(f'共 {len(valid)} 个公式（独立 {display}，行内 {len(valid) - display}），定界错误 {parse_errors}')

    if args.out:
        target = Path(args.out)
        target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已写入 {target}')
    if any(record.get('errors') for record in records):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
