"""Tiny JSON key/value store to hand data between suites.

The admin suite writes the freshly generated Escrow ID; the partner suite
reads it. This keeps the two suites decoupled (each runnable on its own)
while still supporting a single end-to-end chain.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

STORE_PATH = Path(__file__).resolve().parent.parent / "shared" / "artifacts.json"


class ArtifactStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self.path.write_text(json.dumps(data, indent=2))

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        return self._read().get(key, default)
