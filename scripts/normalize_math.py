#!/usr/bin/env python
"""Conservative LaTeX normalization and shared Markdown math scanning.

Ambiguous OCR (unknown commands, multiple/truncated tags) is preserved for
source review. Parsing/compilation can detect errors, not certify accuracy.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import sys
from console_output import safe_print as print
from pathlib import Path

CJK = r'\u4e00-\u9fff'
GREEK = {
    r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
    r'\varepsilon': 'ε', r'\epsilon': 'ε', r'\theta': 'θ', r'\lambda': 'λ',
    r'\mu': 'μ', r'\nu': 'ν', r'\rho': 'ρ', r'\sigma': 'σ', r'\tau': 'τ',
    r'\varphi': 'φ', r'\phi': 'φ', r'\chi': 'χ', r'\psi': 'ψ', r'\omega': 'ω',
    r'\Delta': 'Δ', r'\Sigma': 'Σ', r'\Omega': 'Ω',
}
MISC = {r'\circ': '°', r'\times': '×', r'\cdot': '·', r'\pm': '±', r'\approx': '≈',
        r'\geq': '≥', r'\leq': '≤', r'\infty': '∞', r'\sim': '∼', r'\rightarrow': '→'}
# Match a COMPLETE control word; never split a prefix of a valid macro.
STUCK_COMMANDS = re.compile(r'\\(?:vertHg|cdotkg)(?![A-Za-z])')
TEXT_GROUP = re.compile(r'\\(?:text[a-zA-Z]*|mbox|hbox|operatorname)\*?\s*\{')


def escaped(text: str, index: int) -> bool:
    start = index
    while start and text[start - 1] == '\\':
        start -= 1
    return (index - start) % 2 == 1


def group_end(text: str, start: int) -> int | None:
    """Exclusive end of a balanced brace/bracket/parenthesis group."""
    closing = {'{': '}', '[': ']', '(': ')'}[text[start]]
    depth = 1
    for pos in range(start + 1, len(text)):
        if escaped(text, pos):
            continue
        if text[pos] == text[start]:
            depth += 1
        elif text[pos] == closing:
            depth -= 1
            if not depth:
                return pos + 1
    return None


def _block_ranges(text: str) -> list[tuple[int, int]]:
    ranges = []
    offset, fence, begin = 0, None, 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r' {0,3}(`{3,}|~{3,})(.*)', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                ranges.append((begin, offset + len(line)))
                fence = None
        elif marker and (marker[1][0] != '`' or '`' not in marker[2]):
            fence, begin = marker[1], offset
        elif line.startswith(('    ', '\t')):
            ranges.append((offset, offset + len(line)))
        offset += len(line)
    if fence:
        ranges.append((begin, len(text)))
    return ranges


def _protected_end(text: str, pos: int) -> int | None:
    if escaped(text, pos):
        return None
    if text[pos] == '`':
        run = re.match(r'`+', text[pos:])[0]
        end = re.search(r'(?<!`)`{' + str(len(run)) + r'}(?!`)', text[pos + len(run):])
        if end:
            return pos + len(run) + end.end()
    if text.startswith('![', pos):
        end = group_end(text, pos + 1)
        if end:
            if end < len(text) and text[end] in '([':
                return group_end(text, end) or end
            return end
    # Link destinations and HTML attributes are not formula content.
    if text.startswith('](', pos):
        return group_end(text, pos + 1)
    if text[pos] == '<':
        raw = re.match(r'<(pre|code|script|style)\b[^>]*>', text[pos:], re.I)
        if raw:
            end = re.search(r'</' + raw[1] + r'\s*>', text[pos + raw.end():], re.I)
            return pos + raw.end() + end.end() if end else len(text)
        if text.startswith('<!--', pos):
            end = text.find('-->', pos + 4)
            return end + 3 if end >= 0 else len(text)
        tag = re.match(r'<(?:/?[A-Za-z][^>]*|https?://[^>]*|[^\s<>]+@[^\s<>]+)>', text[pos:])
        if tag:
            return pos + tag.end()
    return None


def protected_markdown(text: str) -> str:
    """Blank non-prose regions, preserving length/newlines for diagnostics.

    Includes code, image alt/destinations and HTML tags. Math remains intact;
    use scan_math on the ORIGINAL text to locate complete math spans.
    """
    masked = list(text)
    ranges = _block_ranges(text)
    block_index, pos = 0, 0
    while pos < len(text):
        while block_index < len(ranges) and ranges[block_index][1] <= pos:
            block_index += 1
        if block_index < len(ranges) and ranges[block_index][0] <= pos:
            end = ranges[block_index][1]
        else:
            end = _protected_end(text, pos)
        if end:
            for index in range(pos, end):
                if text[index] not in '\r\n':
                    masked[index] = ' '
            pos = end
        else:
            pos += 1
    return ''.join(masked)


@dataclass(frozen=True)
class MathSpan:
    source: str
    begin: int
    stop: int
    delimiter: str

    @property
    def display(self) -> bool:
        return self.delimiter in ('$$', r'\[')

    @property
    def body(self) -> str:
        width = len(self.delimiter)
        return self.source[self.begin + width:self.stop - width]

    def start(self) -> int:
        return self.begin

    def end(self) -> int:
        return self.stop

    def group(self, index: int = 0) -> str:
        if index != 0:
            raise IndexError(index)
        return self.source[self.begin:self.stop]


def scan_math(text: str) -> tuple[list[MathSpan], list[dict]]:
    """Scan once, ignoring code, image alt/destinations and escaped dollars.

    Inline math cannot cross lines or protected Markdown. LaTeX text groups
    are opaque: their internal $ and \\(…\\) do not close outer math.
    """
    spans, issues = [], []
    blocks = _block_ranges(text)
    block_index, pos = 0, 0
    while pos < len(text):
        while block_index < len(blocks) and blocks[block_index][1] <= pos:
            block_index += 1
        if block_index < len(blocks) and blocks[block_index][0] <= pos:
            pos = blocks[block_index][1]
            continue
        protected = _protected_end(text, pos)
        if protected:
            pos = protected
            continue
        delimiter = None
        if not escaped(text, pos):
            if text.startswith('$$', pos):
                delimiter = '$$'
            elif text[pos] == '$':
                delimiter = '$'
            elif text.startswith((r'\(', r'\['), pos):
                delimiter = text[pos:pos + 2]
        if delimiter is None:
            pos += 1
            continue
        closing = {r'\(': r'\)', r'\[': r'\]'}.get(delimiter, delimiter)
        cursor = pos + len(delimiter)
        inner_block_index = block_index
        found = False
        while cursor < len(text):
            while inner_block_index < len(blocks) and blocks[inner_block_index][1] <= cursor:
                inner_block_index += 1
            if delimiter in ('$', r'\(') and text[cursor] in '\r\n':
                break
            if inner_block_index < len(blocks) and blocks[inner_block_index][0] <= cursor < blocks[inner_block_index][1]:
                block_start = blocks[inner_block_index][0]
                fenced = re.match(r' {0,3}(?:`{3,}|~{3,})', text[block_start:])
                if delimiter not in ('$$', r'\[') or fenced:
                    break
            # Only text-mode groups can contain inner math dollar delimiters.
            group = TEXT_GROUP.match(text, cursor)
            if group and not escaped(text, cursor):
                end = group_end(text, group.end() - 1)
                if end:
                    cursor = end
                    continue
            if not escaped(text, cursor) and text.startswith(closing, cursor):
                stop = cursor + len(closing)
                spans.append(MathSpan(text, pos, stop, delimiter))
                pos, found = stop, True
                break
            if text[cursor] == '`' and _protected_end(text, cursor):
                break
            cursor += 1
        if not found:
            issues.append({'offset': pos, 'line': text.count('\n', 0, pos) + 1,
                           'error': f'Unpaired math delimiter {delimiter}; Agent must review source',
                           'tex': text[pos:pos + 100]})
            pos += len(delimiter)
    return spans, issues


class _MathPattern:
    """Compatibility interface for callers formerly using regex MATH_SPAN."""
    def __init__(self, inline: bool = False):
        self.inline = inline

    def finditer(self, text: str):
        return iter(s for s in scan_math(text)[0] if not self.inline or not s.display)

    def sub(self, replacement, text: str) -> str:
        result, cursor = [], 0
        for span in self.finditer(text):
            result.extend((text[cursor:span.start()], replacement(span) if callable(replacement) else replacement))
            cursor = span.end()
        return ''.join(result) + text[cursor:]


MATH_SPAN = _MathPattern()
INLINE_MATH = _MathPattern(inline=True)


def _fix_stuck_commands(text: str) -> str:
    return STUCK_COMMANDS.sub(lambda m: {r'\vertHg': r'\vert Hg', r'\cdotkg': r'\cdot kg'}[m[0]], text)


def _dedup_tags(text: str) -> str:
    """Ambiguous tags must survive unchanged for source review."""
    return text


def _fix_arrows_and_tags(text: str) -> str:
    return text


def normalize_math(text: str) -> str:
    """Normalize only explicit known OCR words and scoped font aliases.

    Never inject delimiters or rewrite text-mode contents, arrows or tags.
    Unscoped font declarations already have valid TeX semantics; keep them.
    """
    out, pos = [], 0
    while pos < len(text):
        opaque = TEXT_GROUP.match(text, pos)
        if opaque and not escaped(text, pos):
            end = group_end(text, opaque.end() - 1)
            if end:
                out.append(text[pos:end])
                pos = end
                continue
        if text[pos] == '\\' and not escaped(text, pos):
            command = re.match(r'\\[A-Za-z]+', text[pos:])
            if command:
                word = command[0]
                if word in (r'\rm', r'\it'):
                    next_pos = pos + len(word)
                    brace = next_pos
                    while brace < len(text) and text[brace].isspace():
                        brace += 1
                    if brace < len(text) and text[brace] == '{' and group_end(text, brace):
                        out.append(r'\mathrm' if word == r'\rm' else r'\mathit')
                        pos = next_pos
                        continue
                out.append(_fix_stuck_commands(word))
                pos += len(word)
                continue
        # {\rm ...} is a scoped declaration, including nested braces.
        if text[pos] == '{' and not escaped(text, pos):
            scoped = re.match(r'\{\s*\\(rm|it)\b\s+', text[pos:])
            end = group_end(text, pos)
            if scoped and end:
                name = 'mathrm' if scoped[1] == 'rm' else 'mathit'
                out.append('{\\' + name + '{' + normalize_math(text[pos + scoped.end():end - 1]) + '}}')
                pos = end
                continue
        if text[pos] == '<' and not escaped(text, pos):
            out.append(r'\lt' if pos + 1 < len(text) and text[pos + 1].isspace() else r'\lt ')
            pos += 1
            continue
        if text[pos] == '>' and not escaped(text, pos):
            out.append(r'\gt' if pos + 1 < len(text) and text[pos + 1].isspace() else r'\gt ')
            pos += 1
            continue
        out.append(text[pos])
        pos += 1
    return ''.join(out)


def _math_skeleton(body: str, include_text: bool = False) -> str:
    """Remove text-mode groups and escaped literals before bracket checks."""
    out, pos = [], 0
    while pos < len(body):
        group = TEXT_GROUP.match(body, pos)
        if group:
            end = group_end(body, group.end() - 1)
            if end:
                if include_text:
                    out.append(_math_skeleton(body[group.end():end - 1], include_text=True))
                pos = end
                continue
        command = re.match(r'\\(?:[A-Za-z]+|.)', body[pos:])
        if command:
            # \left. and \right) are intentionally asymmetric delimiters.
            if command[0] in (r'\left', r'\right', r'\middle'):
                return ''
            pos += command.end()
            continue
        out.append(body[pos])
        pos += 1
    return ''.join(out)


def fix_inline_parens(text: str) -> str:
    """Move only an unmatched boundary bracket with a visible prose mate."""
    out, cursor = [], 0
    spans = scan_math(text)[0]
    for index, span in enumerate(spans):
        prose, body = text[cursor:span.start()], span.body
        after = text[span.end():].split('\n', 1)[0]
        after = MATH_SPAN.sub('', after)
        after = after.split('`', 1)[0]
        prefix, suffix = '', ''
        skeleton = _math_skeleton(body)
        visible = _math_skeleton(body, include_text=True)
        text_brackets = any(skeleton.count(c) != visible.count(c) for c in '()[]')
        line_so_far = ''.join(out).rsplit('\n', 1)[-1] + prose
        if not span.display and skeleton and not text_brackets:
            for opening, closing, full_open, full_close in [('(', ')', '（', '）'), ('[', ']', '［', '］')]:
                if body.endswith(closing) and skeleton.count(closing) == skeleton.count(opening) + 1:
                    unmatched_full = line_so_far.count(full_open) > line_so_far.count(full_close)
                    unmatched_ascii = line_so_far.count(opening) > line_so_far.count(closing)
                    if (prose.rstrip().endswith((opening, full_open)) or unmatched_full or unmatched_ascii) and closing not in skeleton[:-1]:
                        suffix = full_close if (prose.rstrip().endswith(full_open) or unmatched_full) else closing
                        body = body[:-1].rstrip()
                        break
                if body.startswith(opening) and skeleton.count(opening) == skeleton.count(closing) + 1:
                    has_mate = bool(re.match(r'[^$\n`]*?' + re.escape(closing) + '|' + r'[^$\n`]*?' + re.escape(full_close), after))
                    if not has_mate:
                        line_tail = text[span.end():].split('\n', 1)[0].split('`', 1)[0]
                        for other_span in spans[index + 1:]:
                            if other_span.start() > span.end() + len(line_tail):
                                break
                            if not other_span.display:
                                other_sk = _math_skeleton(other_span.body)
                                if other_span.body.endswith(closing) and other_sk.count(closing) == other_sk.count(opening) + 1:
                                    has_mate = True
                                    break
                    if has_mate:
                        prefix, body = opening, body[1:].lstrip()
                        break
        close = {r'\(': r'\)', r'\[': r'\]'}.get(span.delimiter, span.delimiter)
        out.extend((prose, prefix, span.delimiter, body, close, suffix))
        cursor = span.end()
    return ''.join(out) + text[cursor:]


def split_leading_prose(tex: str) -> tuple[str | None, str, str | None]:
    """Recover a leading text fragment only with its explicit trailing mate."""
    match = re.match(rf'\s*\\text\s*\{{([^{{}}]*[{CJK}][^{{}}]*)\}}\s*', tex)
    if not match:
        return None, tex, None
    fragment = match[1].strip()
    # Chemical names such as KOH(质量分数 are meaningful text within math.
    # Only a leading Chinese prose fragment is eligible for extraction.
    if not re.match(rf'[{CJK}]', fragment):
        return None, tex, None
    if fragment.count('（') - fragment.count('）') == 1:
        closer = '）'
    elif fragment.count('(') - fragment.count(')') == 1:
        closer = ')'
    else:
        return None, tex, None
    rest = tex[match.end():].rstrip()
    tail = re.search(r'([)）])\s*([。．.、,;；]*)$', rest)
    if not tail or (closer == '）' and tail[1] not in ')）'):
        return None, tex, None
    core = rest[:tail.start()].rstrip()
    if not core or any(_math_skeleton(core).count(a) != _math_skeleton(core).count(b) for a, b in [('(', ')'), ('[', ']')]):
        return None, tex, None
    return fragment, core, closer + tail[2]


def plain_text(text: str) -> str:
    """Lossy plain-text helper for headings/alt; never used on formula bodies."""
    text = re.sub(r'\\(?:mathrm|mathbf|text|operatorname|mathit)\{([^{}]*)\}', r'\1', text)
    symbols = {**GREEK, **MISC}
    text = re.sub(r'\\[A-Za-z]+', lambda m: symbols.get(m[0], m[0]), text)
    text = re.sub(r'\\([a-zA-Z]+)', lambda m: ' ' + m[1] if len(m[1]) > 1 else m[1], text)
    text = text.replace('$', '').replace('^2', '²').replace('^3', '³')
    text = re.sub(r'\s*\{\s*°\s*\}', '°', text)
    text = re.sub(r'[{}]', '', text).replace('*', '')
    return re.sub(r'\s+', ' ', text).strip()


def math_issues(body: str) -> list[str]:
    """Structural issues which a tolerant renderer might otherwise accept."""
    issues = []
    # Text groups are opaque even when they mention a literal \\tag.
    structural = []
    pos = 0
    while pos < len(body):
        group = TEXT_GROUP.match(body, pos)
        if group:
            end = group_end(body, group.end() - 1)
            if end:
                structural.append(' ')
                pos = end
                continue
        structural.append(body[pos])
        pos += 1
    source = ''.join(structural)
    tags = list(re.finditer(r'(?<!\\)\\tag\b\*?', source))
    if len(tags) > 1:
        issues.append('Multiple tags; Agent must review source (no numbering guessed)')
    for tag in tags:
        arg = re.match(r'\s*\{', source[tag.end():])
        start = tag.end() + arg.end() - 1 if arg else -1
        end = group_end(source, start) if arg else None
        if end is None or not source[start + 1:end - 1].strip() or re.search(r'\d\.\s*$', source[start + 1:end - 1]):
            issues.append('Truncated or empty tag; Agent must review source')
    depth = 0
    for pos, char in enumerate(body):
        if escaped(body, pos):
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth < 0:
                break
    if depth:
        issues.append('Unbalanced braces; Agent must review source')
    skeleton = _math_skeleton(body)
    visible = _math_skeleton(body, include_text=True)
    # A visible bracket can legitimately span math and text submodes, e.g.
    # [\\text{Au(NO}_{3})_{3}]. Never repair those group boundaries.
    if any(skeleton.count(a) != skeleton.count(b) and visible.count(a) != visible.count(b)
           for a, b in [('(', ')'), ('[', ']')]):
        issues.append('Unbalanced parentheses/brackets; Agent must review source')
    return issues


def paren_issues(text: str) -> list[str]:
    spans, parse_issues = scan_math(text)
    return [item['error'] for item in parse_issues] + [f'{message}: {span.body[:80]}' for span in spans for message in math_issues(span.body)]


def process(text: str) -> str:
    out, cursor = [], 0
    for span in scan_math(text)[0]:
        out.append(text[cursor:span.start()])
        leading, body, trailing = split_leading_prose(normalize_math(span.body))
        if leading:
            out.append(leading)
        close = {r'\(': r'\)', r'\[': r'\]'}.get(span.delimiter, span.delimiter)
        out.extend((span.delimiter, body, close))
        if trailing:
            out.append(trailing)
        cursor = span.end()
    out.append(text[cursor:])
    return fix_inline_parens(''.join(out))


def main() -> None:
    parser = argparse.ArgumentParser(description='保守公式归一化；歧义必须由 Agent 核对原文')
    parser.add_argument('mode', choices=['fix', 'check'])
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()
    failed = False
    for name in args.files:
        path = Path(name)
        original = path.read_text(encoding='utf-8')
        if args.mode == 'check':
            issues = paren_issues(original)
            failed |= bool(issues)
            print(f'{path.name}: {len(issues)} 个结构/定界问题（编译检查请运行 validate_math.cjs）')
            for item in issues:
                print(f'  {item}')
        else:
            fixed = process(original)
            sys.stdout.write(fixed if len(args.files) == 1 else f'<!-- {path.name} -->\n{fixed}')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
