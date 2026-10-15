"""Print CLI progress without failing on legacy Windows console encodings."""
from __future__ import annotations

import builtins
import sys


def safe_print(*values, sep=' ', end='\n', file=None, flush=False):
    stream = file if file is not None else sys.stdout
    try:
        builtins.print(*values, sep=sep, end=end, file=stream, flush=flush)
    except UnicodeEncodeError:
        encoding = getattr(stream, 'encoding', None) or 'utf-8'
        separator = ' ' if sep is None else sep
        ending = '\n' if end is None else end
        text = separator.join(str(v) for v in values) + ending
        stream.write(text.encode(encoding, errors='backslashreplace').decode(encoding))
        if flush:
            stream.flush()
