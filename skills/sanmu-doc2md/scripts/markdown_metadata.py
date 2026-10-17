"""Identify a leading YAML metadata block without changing source line offsets."""
from __future__ import annotations
import re


def frontmatter_end(text: str) -> int:
    opening = re.match(r'\ufeff?---[ \t]*\r?\n', text)
    if not opening:
        return 0
    closing = re.search(r'^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)', text[opening.end():], re.M)
    return opening.end() + closing.end() if closing else 0


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Parse only complete delimiter lines; malformed leading YAML is an error."""
    import yaml
    end = frontmatter_end(text)
    if not end:
        if re.match(r'\ufeff?---[ \t]*(?:\r?\n|$)', text):
            raise ValueError('Unclosed YAML frontmatter')
        return {}, text
    opening = re.match(r'\ufeff?---[ \t]*\r?\n', text)
    block = text[opening.end():end]
    closing = re.search(r'^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)', block, re.M)
    data = yaml.safe_load(block[:closing.start()])
    if data is not None and not isinstance(data, dict):
        raise ValueError('YAML frontmatter must be a mapping')
    return data or {}, text[end:]


def replace_frontmatter(text: str, metadata: dict) -> str:
    """Replace the same leading block recognized by all Markdown consumers."""
    import yaml
    end = frontmatter_end(text)
    if not end:
        raise ValueError('Missing YAML frontmatter')
    newline = '\r\n' if '\r\n' in text[:end] else '\n'
    head = '---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + '---\n'
    return ('\ufeff' if text.startswith('\ufeff') else '') + head.replace('\n', newline) + text[end:]


def mask_frontmatter(text: str) -> str:
    end = frontmatter_end(text)
    return ''.join(c if c in '\r\n' else ' ' for c in text[:end]) + text[end:]
