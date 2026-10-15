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
import re
from pathlib import Path

MATH_SPAN = re.compile(r'\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)(?:\\.|[^$\n])*?(?<!\\)\$')


def extract(root: Path) -> list[dict]:
    records = []
    for path in sorted(root.rglob('*.md')):
        text = path.read_text(encoding='utf-8')
        for match in MATH_SPAN.finditer(text):
            records.append({
                'file': str(path.relative_to(root)).replace('\\', '/'),
                'tex': match.group(),
                'line': text[:match.start()].count('\n') + 1,
            })
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
    display = sum(1 for r in records if r['tex'].startswith('$$'))
    print(f'共 {len(records)} 个公式（独立 {display}，行内 {len(records) - display}）')

    if args.out:
        target = Path(args.out)
        target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已写入 {target}')


if __name__ == '__main__':
    main()
