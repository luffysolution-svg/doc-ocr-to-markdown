"""HTTP containment and rendered formula-count regressions, no browser dependency."""
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from preview_server import Handler, STATE, render_markdown
from normalize_math import scan_math


class MathNodes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-display' in attrs:
            self.current = {'textContent': '', 'dataset': {'display': attrs['data-display']},
                            'classes': attrs.get('class', '').split()}
            self.nodes.append(self.current)

    def handle_endtag(self, tag):
        if tag in ('span', 'div'):
            self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.current['textContent'] += data


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / 'root'
        self.assets = self.base / 'katex'
        self.root.mkdir()
        self.assets.mkdir()
        (self.base / 'secret.md').write_text('PRIVATE', encoding='utf-8')
        (self.base / 'secret.js').write_text('PRIVATE', encoding='utf-8')
        (self.root / 'images').mkdir()
        (self.root / 'images' / 'a.png').write_bytes(b'PNG')
        (self.assets / 'katex.min.js').write_text('asset', encoding='utf-8')
        self.text = ('# Chapter\n\n`$code$` ![alt $ignored$](images/a.png)\n\n'
                     '$x$ and footnote[^a].\n\n$$y\\tag{1}$$\n\n'
                     '[^a]: Footnote $z$.\n')
        (self.root / '00-目录.md').write_text(self.text, encoding='utf-8')
        self.previous = dict(STATE)
        STATE.update(root=self.root, katex=str(self.assets))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        STATE.clear()
        STATE.update(self.previous)
        self.tmp.cleanup()

    def get(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('GET', path)
        response = connection.getresponse()
        status, content = response.status, response.read()
        connection.close()
        return status, content

    def test_root_images_and_local_assets(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        self.assertEqual(self.get('/images/a.png'), (200, b'PNG'))
        self.assertEqual(self.get('/katex-local/katex.min.js'), (200, b'asset'))
        status, body = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn(b'footnote-ref', body)
        self.assertIn(b'Footnote', body)

    def test_traversal_rejected_for_all_routes(self):
        for route in ['../secret.md', '%2e%2e/secret.md', '..%5csecret.md',
                      '%2e%2e%5csecret.md', '%252e%252e%255csecret.md',
                      'C:%5csecret.md', '%00.md']:
            for prefix in ['/', '/images/', '/katex-local/']:
                with self.subTest(path=prefix + route):
                    status, body = self.get(prefix + route)
                    self.assertEqual(status, 403)
                    self.assertNotIn(b'PRIVATE', body)
        self.assertEqual(self.get('//secret.md')[0], 403)

    def test_symlink_escape_if_supported(self):
        link = self.root / 'outside.md'
        try:
            link.symlink_to(self.base / 'secret.md')
        except OSError:
            self.skipTest('Windows symlink permission unavailable')
        self.assertEqual(self.get('/outside.md')[0], 403)

    def test_shared_scanner_render_nodes_and_error_visibility(self):
        parser = MathNodes()
        parser.feed(render_markdown(self.text))
        spans, errors = scan_math(self.text)
        self.assertEqual(errors, [])
        self.assertEqual(len(parser.nodes), len(spans))
        self.assertEqual(len(parser.nodes), 3)
        self.assertEqual([n['textContent'] for n in parser.nodes], ['x', r'y\tag{1}', 'z'])
        broken = render_markdown('$unpaired\n\n$$x\\tag{1}+y\\tag{2}$$')
        self.assertIn('math-parse-error', broken)
        self.assertIn('Multiple tags', broken)

    def test_actual_page_render_script_counts_real_katex_results(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        env = os.environ.copy()
        modules = Path('C:/Users/Administrator/.agents/skills/doc-ocr-to-markdown/scripts/node_modules')
        if modules.is_dir():
            env['NODE_PATH'] = str(modules) + os.pathsep + env.get('NODE_PATH', '')
        probe = subprocess.run([node, '-e', "require('katex')"], env=env, capture_output=True)
        if probe.returncode:
            self.skipTest('existing KaTeX unavailable')
        page = self.get('/')[1].decode('utf-8')
        parser = MathNodes()
        parser.feed(page)
        script = re.findall(r'<script>(.*?)</script>', page, re.S)[0]
        # Run the exact delivered script against lightweight DOM nodes. The
        # renderer calls real KaTeX rather than accepting every formula.
        harness = r'''
const vm = require('vm');
const katex = require('katex');
const payload = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const nodes = payload.nodes.map(n => ({...n, classList: {
  contains(c) { return n.classes.includes(c); },
  add(c) { n.classes.push(c); }
}}));
const mockDocument = {documentElement:{dataset:{}}, querySelectorAll(selector) {
  return selector === '.math-error' ? nodes.filter(n => n.classList.contains('math-error')) : nodes;
}};
vm.runInNewContext(payload.script, {document:mockDocument, katex: {render(tex, node, options) {
  node.html = katex.renderToString(tex, options);
}}});
console.log(JSON.stringify({dataset:mockDocument.documentElement.dataset,
  actual:nodes.filter(n => n.html && n.html.includes('class="katex"')).length}));
'''
        result = subprocess.run([node, '-e', harness], env=env,
                                input=json.dumps({'nodes': parser.nodes, 'script': script}),
                                text=True, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout)
        self.assertEqual(outcome['actual'], 3)
        self.assertEqual(outcome['dataset'], {'ready': 'true', 'formulas': '3', 'rendered': '3', 'mathErrors': '0'})


if __name__ == '__main__':
    unittest.main()
