#!/usr/bin/env python
"""本地 Markdown + KaTeX 预览服务，用于交付前实际渲染验证。

用法：
    python preview_server.py <知识库目录> [--port 8766] [--katex-dir <katex/dist 路径>]

然后浏览器打开 http://127.0.0.1:<port>/ ，逐章检查图片是否显示、公式是否渲染。
页面会暴露检查结果，便于自动化验证：
    document.documentElement.dataset.ready   # "true" 表示公式渲染完成
    document.querySelectorAll('img')         # 检查 naturalWidth > 0
    document.querySelectorAll('.math-error') # 应为 0

需要 KaTeX 的 CSS/JS：优先用 --katex-dir 指定本地的 katex/dist，
找不到时回退到 CDN（离线环境请指定本地路径）。
"""
from __future__ import annotations

import argparse
import html
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import markdown

MATH_SPAN = re.compile(r'\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)(?:\\.|[^$\n])*?(?<!\\)\$')
CDN = 'https://cdn.jsdelivr.net/npm/katex@0.19.0/dist'
CSS = (
    'body{font-family:"Microsoft YaHei",system-ui,sans-serif;max-width:940px;'
    'margin:36px auto;padding:0 28px;color:#20252b;line-height:1.85;font-size:17px;background:#fff}'
    'h1{font-size:30px}h2{margin-top:2em;border-bottom:1px solid #e3e7eb;padding-bottom:.3em}'
    'img{display:block;max-width:100%;height:auto;margin:1.5em auto}'
    'table{display:block;overflow:auto;border-collapse:collapse;width:100%;font-size:15px}'
    'th,td{border:1px solid #d4dae0;padding:8px 12px;text-align:left}th{background:#f5f7f9}'
    '.math-display{overflow-x:auto;margin:1.6em 0;text-align:center}'
    '.katex{font-size:1.12em}.katex-display{margin:.5em 0}'
    '.math-error{color:#b91c1c;background:#fef2f2;padding:2px 6px;border-radius:3px}'
    'a{color:#146c98}nav{margin-bottom:2em;color:#5b6672}'
)

STATE: dict = {'root': None, 'katex': CDN}


def render_markdown(text: str) -> str:
    """把 Markdown 转 HTML，公式占位后交由 KaTeX 在浏览器端渲染。"""
    formulas: list[tuple[str, str]] = []

    def stash(match):
        raw = match.group()
        display = raw.startswith('$$')
        tex = raw[2:-2] if display else raw[1:-1]
        key = f'ZZMATH{len(formulas):06d}ZZ'
        tag = 'div' if display else 'span'
        cls = 'math-display' if display else 'math-inline'
        formulas.append((
            key,
            f'<{tag} class="{cls}" data-display="{str(display).lower()}">'
            f'{html.escape(tex.strip())}</{tag}>',
        ))
        return f'\n\n{key}\n\n' if display else key

    text = MATH_SPAN.sub(stash, text)
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    for key, value in formulas:
        body = body.replace(f'<p>{key}</p>', value).replace(key, value)
    return body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        request = unquote(self.path.split('?', 1)[0])
        root: Path = STATE['root']

        if request.startswith('/katex-local/'):
            target = (Path(STATE['katex']) / request[len('/katex-local/'):]).resolve()
            self._file(target)
            return
        if request.startswith('/images/'):
            target = (root / request.lstrip('/')).resolve()
            if not target.is_relative_to(root.resolve()):
                self.send_error(403)
                return
            self._file(target)
            return

        target = root / ('00-目录.md' if request == '/' else request.lstrip('/'))
        if request != '/' and not target.suffix:
            target = target.with_suffix('.md')
        if not target.is_file() or target.suffix != '.md':
            self.send_error(404, 'Not found')
            return
        self._markdown(target)

    def _markdown(self, target: Path):
        root: Path = STATE['root']
        body = render_markdown(target.read_text(encoding='utf-8'))
        local = STATE['katex'] != CDN and Path(STATE['katex']).is_dir()
        prefix = '/katex-local' if local else CDN
        page = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<title>{html.escape(target.stem)}</title>'
            f'<link rel="stylesheet" href="{prefix}/katex.min.css">'
            f'<style>{CSS}</style></head><body>'
            + ('<nav><a href="/">返回目录</a></nav>' if str(target) != str(root / '00-目录.md') else '')
            + body
            + f'<script src="{prefix}/katex.min.js"></script>'
            '<script>'
            'for(const node of document.querySelectorAll("[data-display]")){'
            'try{katex.render(node.textContent,node,{displayMode:node.dataset.display==="true",'
            'throwOnError:true,strict:false,trust:false});}'
            'catch(e){node.classList.add("math-error");node.dataset.error=e.message;}}'
            'document.documentElement.dataset.ready="true";'
            '</script></body></html>'
        )
        data = page.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, target: Path):
        if not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description='Markdown + KaTeX 本地预览')
    parser.add_argument('root', help='知识库目录')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--katex-dir', help='本地 katex/dist 目录（离线时使用）')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f'目录不存在：{root}')
    STATE['root'] = root
    if args.katex_dir:
        STATE['katex'] = str(Path(args.katex_dir).resolve())

    print(f'预览地址：http://127.0.0.1:{args.port}/')
    print(f'知识库：{root}')
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
