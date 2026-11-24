"""Terminal output shared by every command."""

import os
import sys
from pathlib import Path
from typing import TextIO

NEXT_STEPS_HEADING = "── Next steps for you ──"


def say(status: str, message: str) -> None:
    print(f"{status:<10}{message}")


def next_steps(steps: list[str], file: TextIO | None = None) -> None:
    if not steps:
        return
    file = file or sys.stdout  # resolved per call, not at import
    print(f"\n{NEXT_STEPS_HEADING}", file=file)
    for i, step in enumerate(steps, 1):
        print(f"{i}. {step}", file=file)


def confirm(question: str, default: bool = True) -> bool:
    """Yes/no prompt; Enter means `default`."""
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return False
    return default if not answer else answer in ("y", "yes")


def tilde(p: Path) -> str:
    home, s = str(Path.home()), str(p)
    return "~" + s[len(home):] if s.startswith(home + os.sep) else s
