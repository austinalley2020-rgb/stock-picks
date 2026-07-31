"""Load / save positions from JSON or Python dicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .position import Position


def load_position(path: str | Path) -> Position:
    path = Path(path)
    data = json.loads(path.read_text())
    return Position.from_dict(data)


def save_position(position: Position, path: str | Path) -> None:
    path = Path(path)
    path.write_text(json.dumps(position.to_dict(), indent=2) + "\n")


def position_from_python(spec: dict[str, Any]) -> Position:
    """Convenience for notebooks: pass a plain dict."""
    return Position.from_dict(spec)
