#!/usr/bin/env python
"""MinerU 云端 OCR：提交整批文件、轮询、下载结果。

用法：
    # 提交并等待（最常见）
    python mineru_ocr.py submit --out-root ./ocr-out file1.pdf file2.pdf ...

    # 一份文件一个 data_id 时，用 --id-file 指定（每行 name<TAB>data_id）
    python mineru_ocr.py submit --out-root ./ocr-out --id-file ids.tsv

    # 已提交过，继续等或补下载
    python mineru_ocr.py resume --out-root ./ocr-out

    # 查状态但不下载
    python mineru_ocr.py status --out-root ./ocr-out

Token 从环境变量 MINERU_API_TOKEN 读取，不要硬编码进文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import requests
from console_output import safe_print as print

BASE = 'https://mineru.net/api/v4'
DOH = 'https://dns.alidns.com/resolve'


def token() -> str:
    value = os.environ.get('MINERU_API_TOKEN')
    if not value:
        sys.exit('未设置环境变量 MINERU_API_TOKEN')
    return value


def api(method: str, endpoint: str, body: dict | None = None) -> dict:
    response = requests.request(
        method, BASE + endpoint,
        headers={'Authorization': 'Bearer ' + token()},
        json=body, timeout=90)
    response.raise_for_status()
    data = response.json()
    if data.get('code') != 0:
        raise RuntimeError(f'MinerU 请求失败 {data.get("code")}: {data.get("msg")}')
    return data['data']


def real_ips(host: str) -> list[str]:
    """结果 CDN 可能被代理 fake-IP 劫持，用 DoH 取真实地址。"""
    try:
        answer = requests.get(
            DOH, params={'name': host, 'type': 'A'}, timeout=30).json()
        ips = [r['data'] for r in answer.get('Answer', []) if r.get('type') == 1]
        if ips:
            return ips
    except Exception:
        pass
    return []


def download(url: str, target: Path) -> None:
    """下载结果 zip，必要时用 DoH 真实 IP 绕过 fake-IP。"""
    if target.exists() and zipfile.is_zipfile(target):
        return
    host = urlsplit(url).hostname or ''
    attempts: list[list[str]] = [[]]
    ips = real_ips(host)
    if ips:
        attempts = [[ip] for ip in ips[:4]] + [[]]

    last_error = None
    for group in attempts:
        for ip in (group or [None]):
            if ip:
                cmd = ['curl', '--resolve', f'{host}:443:{ip}']
            else:
                cmd = ['curl']
            # 保留 TLS 校验：不用 -k，不改系统代理
            cmd += ['--connect-timeout', '20', '--max-time', '600',
                    '--fail', '--silent', '--show-error',
                    '-L', '-o', str(target), url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and target.exists() and zipfile.is_zipfile(target):
                return
            last_error = result.stderr.strip() or f'curl exit {result.returncode}'
    raise RuntimeError(f'结果下载失败：{last_error}')


def valid_id(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('data_id 必须是 1–64 个英文字母、数字、下划线或连字符，且以字母或数字开头')
    return value


def submit(args) -> None:
    out_root = Path(args.out_root).resolve()
    if (out_root / 'job.json').exists():
        raise ValueError('工作目录已有 job.json；用 resume 继续，或为新批次选择新目录')
    out_root.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    ids: list[str] = []
    if args.id_file:
        for line in Path(args.id_file).read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            name, _, data_id = line.partition('\t')
            path = Path(name.strip())
            files.append(path)
            ids.append(data_id.strip() or path.stem)
    else:
        files = [Path(p) for p in args.files]
        ids = [f'f{i:02d}' for i in range(1, len(files) + 1)]

    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        sys.exit('文件不存在：\n  ' + '\n  '.join(missing))
    if not files:
        sys.exit('没有输入文件')

    for data_id in ids:
        valid_id(data_id)
    if len(set(ids)) != len(ids):
        raise ValueError('data_id 重复；每份输入必须有独立标识')
    specs = [{'name': f.name, 'data_id': d, 'is_ocr': True} for f, d in zip(files, ids)]
    data = api('POST', '/file-urls/batch', {
        'files': specs,
        'model_version': args.model,
        'enable_formula': True,
        'enable_table': True,
        'language': args.language,
    })
    job = {'batch_id': data['batch_id'],
           'files': [{'path': str(f.resolve()), 'data_id': d} for f, d in zip(files, ids)]}
    (out_root / 'job.json').write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'批次：{data["batch_id"]}，共 {len(files)} 个文件')

    def upload(pair):
        path, url = pair
        with path.open('rb') as stream:
            # 原始二进制，不带 Authorization，不用 multipart
            resp = requests.put(url, data=stream, timeout=600)
            resp.raise_for_status()
        print(f'  已上传 {path.name}', flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(upload, zip(files, data['file_urls'])))

    collect(out_root, job, interval=args.interval, timeout=args.timeout, quiet=args.quiet)


def collect(out_root: Path, job: dict, interval: int, timeout: int, quiet: bool) -> None:
    target_root = out_root / 'raw'
    target_root.mkdir(parents=True, exist_ok=True)
    wanted = {item['data_id'] for item in job['files']}
    done: set[str] = set()
    previous = None
    deadline = time.time() + timeout

    while time.time() < deadline:
        data = api('GET', f'/extract-results/batch/{job["batch_id"]}')
        results = data.get('extract_result', [])
        (out_root / 'status.json').write_text(
            json.dumps([{k: v for k, v in r.items() if not k.endswith('url')}
                        for r in results], ensure_ascii=False, indent=2), encoding='utf-8')

        snapshot = [(r.get('data_id'), r.get('state'),
                     (r.get('extract_progress') or {}).get('extracted_pages')) for r in results]
        if not quiet and snapshot != previous:
            for data_id, state, pages in snapshot:
                print(f'  {data_id}: {state}{f" ({pages} 页)" if pages else ""}', flush=True)
            previous = snapshot

        for result in results:
            key = valid_id(result.get('data_id') or Path(result['file_name']).stem)
            if key not in wanted:
                raise ValueError('结果包含未提交的 data_id')
            state = result.get('state')
            if state == 'failed':
                raise RuntimeError(f'{key} 识别失败：{result.get("err_msg")}')
            if state != 'done' or key in done:
                continue
            folder = target_root / key
            if not (folder / '_ok').exists():
                archive = target_root / f'{key}.zip'
                download(result['full_zip_url'], archive)
                folder.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive) as zf:
                    for name in zf.namelist():
                        dest = (folder / name).resolve()
                        if not dest.is_relative_to(folder.resolve()):
                            raise ValueError(f'压缩包含非法路径：{name}')
                    zf.extractall(folder)
                (folder / '_ok').touch()
            done.add(key)
            print(f'  已下载 {key}', flush=True)

        if wanted <= done:
            print(f'完成：{len(done)}/{len(wanted)}')
            return
        time.sleep(interval)

    raise SystemExit(f'超时。可稍后运行 resume 继续：{out_root}')


def resume(args) -> None:
    out_root = Path(args.out_root).resolve()
    job_file = out_root / 'job.json'
    if not job_file.exists():
        sys.exit(f'找不到 {job_file}，请先 submit')
    job = json.loads(job_file.read_text(encoding='utf-8'))
    collect(out_root, job, interval=args.interval, timeout=args.timeout, quiet=args.quiet)


def status(args) -> None:
    out_root = Path(args.out_root).resolve()
    job_file = out_root / 'job.json'
    if not job_file.exists():
        sys.exit(f'找不到 {job_file}')
    job = json.loads(job_file.read_text(encoding='utf-8'))
    data = api('GET', f'/extract-results/batch/{job["batch_id"]}')
    for result in data.get('extract_result', []):
        progress = result.get('extract_progress') or {}
        pages = progress.get('extracted_pages')
        total = progress.get('total_pages')
        suffix = f' ({pages}/{total} 页)' if pages else ''
        print(f'{result.get("data_id")}: {result.get("state")}{suffix}')


def split_pdf(args) -> None:
    try:
        import fitz
    except ImportError:
        sys.exit('拆分超长 PDF 需要安装 PyMuPDF: pip install pymupdf')
    src = Path(args.file).resolve()
    if not src.is_file():
        sys.exit(f'文件不存在: {src}')
    chunk_size = args.chunk_size
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError('chunk-size 必须为正整数')
    prefix = valid_id(args.prefix or 'part')
    out_root = Path(args.out_root).resolve()
    chunks_dir = out_root / 'chunks'
    if any((out_root / name).exists() for name in ('manifest.json', 'ids.tsv', 'chunks')):
        raise ValueError('拆分目标已有产物；请使用新的工作目录，避免覆盖')
    doc = fitz.open(src)
    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError('PDF 没有页面')
    chunks_dir.mkdir(parents=True, exist_ok=False)
    manifest = []
    ids = []

    for index, start in enumerate(range(0, total_pages, chunk_size), 1):
        end = min(start + chunk_size, total_pages)
        key = f'{prefix}{index:02d}'
        target = chunks_dir / f'{key}.pdf'
        part = fitz.open()
        part.insert_pdf(doc, from_page=start, to_page=end - 1)
        part.save(target, garbage=3, deflate=True)
        part.close()
        manifest.append({
            'id': key,
            'start_page': start + 1,
            'end_page': end,
            'pages': end - start,
            'path': str(target),
        })
        ids.append(f'{target}\t{key}')

    doc.close()
    (out_root / 'manifest.json').write_text(
        json.dumps({'source': str(src), 'pages': total_pages, 'chunk_size': chunk_size, 'parts': manifest},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    (out_root / 'ids.tsv').write_text('\n'.join(ids), encoding='utf-8')
    print(f'已将 {src.name}（共 {total_pages} 页）拆分为 {len(manifest)} 个分片，保存在 {chunks_dir}')
    print(f'已生成清单：{out_root / "manifest.json"} 与 {out_root / "ids.tsv"}')
    print(f'接下来可直接运行：python mineru_ocr.py submit --out-root {out_root} --id-file {out_root / "ids.tsv"}')


def main() -> None:
    parser = argparse.ArgumentParser(description='MinerU 云端 OCR')
    sub = parser.add_subparsers(dest='command', required=True)

    def common(p):
        p.add_argument('--out-root', required=True, help='工作目录（放 job.json 与 raw/）')
        p.add_argument('--interval', type=int, default=15, help='轮询间隔秒')
        p.add_argument('--timeout', type=int, default=7200, help='总超时秒')
        p.add_argument('--quiet', action='store_true')

    sp = sub.add_parser('split', help='将超长教材/大 PDF 拆分为合规分片并生成 manifest')
    sp.add_argument('file', help='待拆分的大 PDF 路径')
    sp.add_argument('--out-root', required=True, help='工作目录（输出 chunks/, manifest.json, ids.tsv）')
    sp.add_argument('--chunk-size', type=int, default=96, help='单分片最大页数（默认 96 页）')
    sp.add_argument('--prefix', default='part', help='分片 id 前缀（如 part01）')
    sp.set_defaults(func=split_pdf)

    s = sub.add_parser('submit', help='提交新批次')
    s.add_argument('files', nargs='*', help='待识别文件')
    s.add_argument('--id-file', help='每行 name<TAB>data_id，替代位置参数')
    s.add_argument('--model', default='vlm', choices=['vlm', 'pipeline'])
    s.add_argument('--language', default='ch')
    common(s)
    s.set_defaults(func=submit)

    r = sub.add_parser('resume', help='继续已有批次')
    common(r)
    r.set_defaults(func=resume)

    t = sub.add_parser('status', help='查看状态')
    t.add_argument('--out-root', required=True)
    t.set_defaults(func=status)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
