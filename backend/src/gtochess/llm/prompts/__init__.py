from __future__ import annotations

from functools import cache
from pathlib import Path

DIRECTORY = Path(__file__).parent


@cache
def prompt(name: str) -> str:
    path = DIRECTORY / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt named {name!r} in {DIRECTORY}")
    return path.read_text(encoding="utf-8").strip()
