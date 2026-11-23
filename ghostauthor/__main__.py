"""``python -m ghostauthor`` — the same entry point as the console script.

Exists so that the three documented ways of starting the tool agree:
``ghostauthor``, ``python -m ghostauthor`` and ``python -m ghostauthor.cli``.
Packaging tests install the wheel and run the console script; this module
covers the invocation people try when the script is not on PATH (a venv
without activation, ``python -m`` inside CI, an editor task runner).
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
