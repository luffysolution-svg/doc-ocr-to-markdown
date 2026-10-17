"""Shared CommonMark/Obsidian/HTML resource parsing and protected source spans.

Resource destinations are decoded only during resolution. Public paths stay logical
(no Windows extended-path prefixes). Missing/ambiguous local targets raise ValueError.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import html
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from portable_paths import safe_path, walk_files, read_text


def markdown_parser():
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise RuntimeError('Missing dependency markdown-it-py; resource validation cannot run') from exc
    from mdit_py_plugins.footnote import footnote_plugin
    parser = MarkdownIt('commonmark', {'html': True}).enable('table').use(footnote_plugin, move_to_end=False)

    def wiki(state, silent):
        match = re.match(r'(!?)\[\[([^\n]+?)\]\]', state.src[state.pos:])
        if not match:
            return False
        if not silent:
            token = state.push('wiki_embed' if match[1] else 'wiki_link', '', 0)
            target, _, alias = match[2].partition('|')
            token.attrs = {'src' if match[1] else 'href': target}
            token.content = alias or target
        state.pos += match.end()
        return True

    parser.inline.ruler.before('image', 'obsidian_wiki', wiki)

    def render_wiki(tokens, idx, options, env):
        token = tokens[idx]
        attr = 'src' if token.type == 'wiki_embed' else 'href'
        from urllib.parse import quote
        destination = token.attrGet(attr) or ''
        target, marker, fragment = destination.partition('#')
        dest = quote(target, safe='/') + (('#' + quote(fragment)) if marker else '')
        label = html.escape(token.content, quote=True)
        if token.type == 'wiki_embed':
            return f'<img src="{html.escape(dest, quote=True)}" alt="{label}">'
        return f'<a href="{html.escape(dest, quote=True)}">{label}</a>'

    def footnote_open(tokens, idx, options, env):
        label = tokens[idx].meta['label']
        item = env.get('footnotes', {}).get('refs', {}).get(':' + label, -1)
        identifier = f'fn{item + 1}' if item >= 0 else 'fn-' + label
        return f'<aside class="footnote" id="{html.escape(identifier, quote=True)}">'

    parser.renderer.rules['footnote_reference_open'] = footnote_open
    parser.renderer.rules['footnote_reference_close'] = lambda tokens, idx, options, env: '</aside>\n'
    parser.renderer.rules['wiki_link'] = render_wiki
    parser.renderer.rules['wiki_embed'] = render_wiki
    return parser


def code_ranges(text: str) -> list[tuple[int, int]]:
    """Frontmatter and parser-classified block code, using original offsets."""
    from markdown_metadata import frontmatter_end
    end = frontmatter_end(text)
    offsets = [0]
    for match in re.finditer('\n', text):
        offsets.append(match.end())
    offsets.append(len(text))
    masked = ''.join('\n' if c == '\n' else ' ' for c in text[:end]) + text[end:]
    ranges = [(0, end)] if end else []
    for token in markdown_parser().parse(masked):
        if token.type in ('fence', 'code_block') and token.map:
            a, b = token.map
            ranges.append((offsets[a], offsets[min(b, len(offsets) - 1)]))
    return sorted(ranges)


def _merge(ranges):
    merged = []
    for start, stop in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
        else:
            merged.append((start, stop))
    return merged


def _reference_definitions(text):
    # CommonMark allows a destination/title on following lines. Blank only actual
    # definitions (footnotes are prose, not link reference definitions).
    pattern = r'(?m)^ {0,3}\[(?!\^)([^\]\n]+)\]:[ \t]*(?:\n[ \t]*)?(?:<[^>\n]*>|\S+)(?:[ \t]+(?:"[^"\n]*"|\x27[^\x27\n]*\x27|\([^\n]*\)))?[ \t]*(?:\n[ \t]+(?:"[^"\n]*"|\x27[^\x27\n]*\x27|\([^\n]*\)))?'
    return list(re.finditer(pattern, text))


def protected_ranges(text: str) -> list[tuple[int, int]]:
    """Half-open source ranges safe to leave intact during prose/SI rewriting."""
    from normalize_math import _protected_end, escaped, group_end
    ranges = code_ranges(text)
    definitions = _reference_definitions(text)
    ranges.extend((m.start(), m.end()) for m in definitions)
    labels = {' '.join(m[1].split()).casefold() for m in definitions}
    pos = 0
    blocks = _merge(ranges)
    index = 0
    while pos < len(text):
        while index < len(blocks) and blocks[index][1] <= pos:
            index += 1
        if index < len(blocks) and blocks[index][0] <= pos:
            pos = blocks[index][1]
            continue
        end = _protected_end(text, pos)
        if end:
            ranges.append((pos, end))
            pos = end
            continue
        if not escaped(text, pos):
            wiki = re.match(r'!?\[\[[^\n]+?\]\]', text[pos:])
            if wiki:
                end = pos + wiki.end()
                ranges.append((pos, end))
                pos = end
                continue
            if text[pos] == '[':
                end = group_end(text, pos)
                if end:
                    label = ' '.join(text[pos + 1:end - 1].split()).casefold()
                    stop = None
                    if end < len(text) and text[end] == '(':
                        stop = group_end(text, end)
                    elif end < len(text) and text[end] == '[':
                        ref_end = group_end(text, end)
                        if ref_end:
                            ref = ' '.join(text[end + 1:ref_end - 1].split()).casefold() or label
                            if ref in labels:
                                stop = ref_end
                    elif label in labels or label.startswith('^'):
                        stop = end
                    if stop:
                        ranges.append((pos, stop))
                        pos = stop
                        continue
        pos += 1
    # Preserve entire HTML anchor contents, not only its attributes/tags.
    for match in re.finditer(r'<a\b[^>]*>[\s\S]*?</a\s*>', text, re.I):
        ranges.append((match.start(), match.end()))
    return _merge(ranges)


def rewrite_unprotected(text: str, callback) -> str:
    pieces, cursor = [], 0
    for start, stop in protected_ranges(text):
        pieces.extend((callback(text[cursor:start]), text[start:stop]))
        cursor = stop
    return ''.join(pieces) + callback(text[cursor:])


@dataclass(frozen=True)
class Resource:
    kind: str
    destination: str
    label: str
    syntax: str


class _HTMLResources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.resources = []
        self.anchors = set()
        self.opaque = []

    def handle_endtag(self, tag):
        if self.opaque and self.opaque[-1] == tag:
            self.opaque.pop()

    def handle_starttag(self, tag, attrs):
        if tag in ('pre', 'code', 'script', 'style'):
            self.opaque.append(tag)
        if self.opaque:
            return
        attrs = dict(attrs)
        if attrs.get('id'):
            self.anchors.add(attrs['id'])
        if tag == 'a' and attrs.get('name'):
            self.anchors.add(attrs['name'])
        if tag == 'img' and attrs.get('src') is not None:
            self.resources.append(Resource('image', attrs['src'], attrs.get('alt', ''), 'html'))
        if tag == 'a' and attrs.get('href') is not None:
            self.resources.append(Resource('link', attrs['href'], '', 'html'))
        if tag == 'source' and attrs.get('src') is not None:
            self.resources.append(Resource('image', attrs['src'], '', 'html'))
        # Each srcset candidate is a separate required resource.
        if tag in ('img', 'source') and attrs.get('srcset'):
            for item in attrs['srcset'].split(','):
                if item.strip():
                    self.resources.append(Resource('image', item.strip().split()[0], attrs.get('alt', ''), 'html'))

    handle_startendtag = handle_starttag


def mask_math(text: str) -> str:
    from normalize_math import scan_math
    chars = list(text)
    for span in scan_math(text)[0]:
        for i in range(span.start(), span.end()):
            if chars[i] not in '\r\n':
                chars[i] = ' '
    return ''.join(chars)


def parse_resources(text: str) -> list[Resource]:
    """Actual tokens only: excludes examples, math, metadata and undefined refs."""
    from markdown_metadata import mask_frontmatter
    source = mask_math(mask_frontmatter(text))
    # Raw HTML code blocks can contain literal img/a markup. The CommonMark
    # token retains it as HTML rather than code, so exclude the opaque region.
    source = re.sub(r'<(pre|code|script|style)\b[^>]*>[\s\S]*?</\1\s*>',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), source, flags=re.I)
    tokens = markdown_parser().parse(source)
    resources = []

    def visit(token):
        if token.type in ('image', 'link_open', 'wiki_link', 'wiki_embed'):
            kind = 'image' if token.type in ('image', 'wiki_embed') else 'link'
            resources.append(Resource(kind, token.attrGet('src' if kind == 'image' else 'href') or '',
                                      token.content, 'wiki' if token.type.startswith('wiki_') else 'markdown'))
        if token.type in ('html_inline', 'html_block'):
            parser = _HTMLResources()
            parser.feed(token.content)
            resources.extend(parser.resources)
        for child in token.children or []:
            visit(child)

    for token in tokens:
        visit(token)
    return resources


def resolve_resource(resource: Resource, source: Path, root: Path, *, resource_root: Path | None = None) -> Path | None:
    """Resolve local targets; wiki basenames may uniquely match sibling SI notes."""
    destination = html.unescape(resource.destination)
    parts = urlsplit(destination)
    if parts.scheme or parts.netloc:
        # Drive paths are never remote URLs.
        if re.match(r'^[A-Za-z]:', destination):
            raise ValueError(f'Absolute path is forbidden: {destination}')
        return None
    decoded = unquote(parts.path).replace('\\', '/')
    if decoded.startswith('/'):
        raise ValueError(f'Absolute path is forbidden: {destination}')
    if not decoded:
        return source.resolve()
    target = (source.parent / decoded).resolve()
    scope = (resource_root or root).resolve()
    if resource.syntax == 'wiki':
        if not target.is_relative_to(scope):
            raise ValueError(f'Wikilink escapes resource boundary: {destination}')
    if safe_path(target).is_file():
        return target
    if resource.syntax == 'wiki':
        if not Path(decoded).suffix:
            candidate = Path(str(target) + '.md')
            if safe_path(candidate).is_file():
                return candidate
        # Limit cross-document lookup to the output root and its designated resource scope.
        names = {decoded, decoded + '.md'} if not Path(decoded).suffix else {decoded}
        matches = [p.resolve() for p in walk_files(scope)
                   if p.name in names and p.is_relative_to(scope)]
        matches = sorted(set(matches))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f'Ambiguous wikilink: {destination}')
    raise ValueError(f'Missing resource: {destination}')


def anchor_ids(text: str) -> set[str]:
    from markdown_metadata import mask_frontmatter
    source = mask_frontmatter(text)
    anchors, counts = set(), {}
    tokens = markdown_parser().parse(source)
    for i, token in enumerate(tokens):
        if token.type == 'heading_open' and i + 1 < len(tokens):
            inline = tokens[i + 1]
            title = ''.join(c.content for c in inline.children or [] if c.type not in ('html_inline',))
            title = html.unescape(title).strip()
            anchors.add(title)  # Obsidian uses the heading itself.
            slug = re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')
            count = counts.get(slug, 0)
            counts[slug] = count + 1
            anchors.add(slug + (f'-{count}' if count else ''))
        if token.type in ('html_inline', 'html_block'):
            parser = _HTMLResources()
            parser.feed(token.content)
            anchors.update(parser.anchors)
        for child in token.children or []:
            if child.type == 'html_inline':
                parser = _HTMLResources()
                parser.feed(child.content)
                anchors.update(parser.anchors)
    visible = list(source)
    for start, stop in code_ranges(source):
        visible[start:stop] = ' ' * (stop - start)
    anchors.update('^' + m[1] for m in re.finditer(r'(?m)(?:^|\s)\^([\w-]+)\s*$', ''.join(visible)))
    return anchors


def check_anchor(resource: Resource, target: Path) -> bool:
    fragment = unquote(urlsplit(resource.destination).fragment)
    if not fragment:
        return True
    if target.suffix.lower() != '.md':
        return True  # Binary resources have format-specific fragments.
    return fragment in anchor_ids(read_text(target))
