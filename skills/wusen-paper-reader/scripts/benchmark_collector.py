"""Cross-literature benchmark structured collector and ledger.

Manages, deduplicates, and logs standardized catalytic and materials performance
benchmarks into a persistent CSV/JSON ledger across literature reviews.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

BENCHMARK_FIELDS = [
    "doi",
    "citation_key",
    "year",
    "journal",
    "catalyst_system",
    "reaction_type",
    "activity_rate",
    "activity_unit",
    "aqy_percent",
    "light_condition",
    "scavenger_or_electrolyte",
    "benchmark_comparison",
    "mechanism",
    "record_date"
]


class BenchmarkError(Exception):
    pass


def load_benchmarks(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_benchmarks(csv_path: Path, rows: List[Dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=BENCHMARK_FIELDS)
        writer.writeheader()
        for r in rows:
            # normalize row with all fields
            row_dict = {field: r.get(field, "") for field in BENCHMARK_FIELDS}
            writer.writerow(row_dict)


def upsert_benchmark(csv_path: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    doi = str(entry.get("doi", "")).strip().lower()
    citation_key = str(entry.get("citation_key", "")).strip()

    if not doi and not citation_key:
        raise BenchmarkError("Benchmark entry must contain at least a 'doi' or 'citation_key'.")

    rows = load_benchmarks(csv_path)
    updated = False

    cleaned_entry = {k: str(entry.get(k, "")).strip() for k in BENCHMARK_FIELDS}
    if not cleaned_entry.get("record_date"):
        cleaned_entry["record_date"] = str(date.today())

    new_rows = []
    for r in rows:
        r_doi = r.get("doi", "").strip().lower()
        r_key = r.get("citation_key", "").strip()

        # match by DOI or citation key
        if (doi and r_doi == doi) or (citation_key and r_key == citation_key):
            new_rows.append(cleaned_entry)
            updated = True
        else:
            new_rows.append(r)

    if not updated:
        new_rows.append(cleaned_entry)

    save_benchmarks(csv_path, new_rows)
    return {
        "status": "updated" if updated else "added",
        "total_records": len(new_rows),
        "entry": cleaned_entry
    }


def export_markdown_table(csv_path: Path) -> str:
    rows = load_benchmarks(csv_path)
    if not rows:
        return "*暂无 Benchmark 性能记录。*"

    headers = [
        "催化体系", "年份/期刊", "反应类型", "活性速率", "AQY (%)", "光源/条件", "牺牲剂/电解液", "对比/机制"
    ]
    md_lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |"
    ]

    for r in rows:
        sys_name = r.get("catalyst_system", "-")
        year_j = f"{r.get('year', '')} {r.get('journal', '')}".strip()
        react = r.get("reaction_type", "-")
        rate = f"{r.get('activity_rate', '')} {r.get('activity_unit', '')}".strip()
        aqy = r.get("aqy_percent", "-")
        light = r.get("light_condition", "-")
        scav = r.get("scavenger_or_electrolyte", "-")
        mech = f"{r.get('benchmark_comparison', '')}; {r.get('mechanism', '')}".strip(" ;")

        md_lines.append(f"| **{sys_name}** | {year_j} | {react} | {rate} | {aqy} | {light} | {scav} | {mech} |")

    return "\n".join(md_lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Collector Tool")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    add_p = subparsers.add_parser("add", help="Add or update a benchmark entry")
    add_p.add_argument("csv_path", help="Path to benchmarks.csv")
    add_p.add_argument("--json-entry", required=True, help="JSON string representing the benchmark record")

    export_p = subparsers.add_parser("export-md", help="Export benchmarks as Markdown table")
    export_p.add_argument("csv_path", help="Path to benchmarks.csv")
    export_p.add_argument("--out", help="Output file path")

    list_p = subparsers.add_parser("list", help="List all benchmark entries")
    list_p.add_argument("csv_path", help="Path to benchmarks.csv")

    args = parser.parse_args(argv)

    try:
        csv_path = Path(args.csv_path)
        if args.subcommand == "add":
            data = json.loads(args.json_entry)
            res = upsert_benchmark(csv_path, data)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif args.subcommand == "export-md":
            table_md = export_markdown_table(csv_path)
            if args.out:
                Path(args.out).write_text(table_md, encoding="utf-8")
                print(f"Exported to {args.out}")
            else:
                print(table_md)
        elif args.subcommand == "list":
            rows = load_benchmarks(csv_path)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
