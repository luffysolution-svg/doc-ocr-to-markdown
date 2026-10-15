#!/usr/bin/env python
"""Run the conservative math/preview regression suite; assertion failures fail CI."""
from pathlib import Path
import sys
import unittest


def main() -> None:
    tests = Path(__file__).resolve().parents[1] / 'tests'
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.discover(str(tests), pattern=pattern)
        for pattern in ('test_math.py', 'test_preview.py')
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
