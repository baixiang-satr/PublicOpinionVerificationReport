"""Desktop application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from src.ui.app import run_app
    except ImportError as error:
        if error.name and error.name.startswith("PyQt5"):
            print("缺少 PyQt5，请先运行：pip install -r requirements.txt", file=sys.stderr)
            return 2
        raise
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
