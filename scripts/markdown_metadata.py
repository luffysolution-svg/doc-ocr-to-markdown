"""Identify a leading YAML metadata block without changing source line offsets."""
from __future__ import annotations
import re


def frontmatter_end(text: str) -> int:
    opening = re.match(r'\ufeff?---[ \t]*\r?\n', text)
    if not opening:
        return 0
    closing = re.search(r'^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)', text[opening.end():], re.M)
    return opening.end() + closing.end() if closing else 0


def mask_frontmatter(text: str) -> str:
    end = frontmatter_end(text)
    return ''.join(c if c in '\r\n' else ' ' for c in text[:end]) + text[end:]
