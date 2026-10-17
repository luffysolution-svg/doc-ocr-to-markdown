"""Extended Windows paths for filesystem access, with ordinary paths in reports."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path


def safe_path(path: str | os.PathLike) -> Path:
    path = os.fspath(path)
    if os.name != 'nt' or path.startswith('\\\\?\\'):
        return Path(path)
    absolute = os.path.abspath(path)
    if absolute.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + absolute[2:])
    return Path('\\\\?\\' + absolute)


def logical_path(path: str | os.PathLike) -> Path:
    value = os.fspath(path)
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return Path(value)


def walk_files(root: str | os.PathLike, pattern: str = '*') -> list[Path]:
    """Enumerate without following directory links or suppressing access errors."""
    root = Path(root).absolute()
    files: list[Path] = []
    if safe_path(root).is_file():
        return [root] if fnmatch.fnmatch(root.name, pattern) else []
    if not safe_path(root).is_dir():
        raise FileNotFoundError(root)

    def walk(directory: Path) -> None:
        with os.scandir(safe_path(directory)) as entries:
            for entry in entries:
                path = directory / entry.name
                if entry.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(entry.path)):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(path)
                elif entry.is_file(follow_symlinks=False) and fnmatch.fnmatch(entry.name, pattern):
                    files.append(path)
    walk(root)
    return sorted(files)


def read_text(path: str | os.PathLike, encoding: str = 'utf-8', **kwargs) -> str:
    return safe_path(path).read_text(encoding=encoding, **kwargs)


def write_text(path: str | os.PathLike, text: str, encoding: str = 'utf-8', **kwargs) -> int:
    return safe_path(path).write_text(text, encoding=encoding, **kwargs)
