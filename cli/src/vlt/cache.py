"""Content-addressed store. Every stage result lives at <root>/<namespace>/<key>.json.

A key is the hash of the stage's input, so a result exists iff that exact input was processed.
symbols.json is the one mutable file; add_symbols is its only writer and holds a file lock,
so parallel scrubs never assign one placeholder twice.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("VLT_HOME", Path.home() / ".vlt"))


def h(obj: Any) -> str:
    data = obj if isinstance(obj, (bytes, str)) else json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def chain_keys(steps: list[dict]) -> list[str]:
    """chain_keys[i] covers steps[0..i]; a session that grows keeps every earlier key."""
    keys, prev = [], ""
    for step in steps:
        prev = h(prev + h(step))
        keys.append(prev)
    return keys


class Store:
    def __init__(self, root: Path = ROOT):
        self.root = root

    def _path(self, ns: str, key: str) -> Path:
        return self.root / ns / f"{key}.json"

    def get(self, ns: str, key: str) -> Any | None:
        p = self._path(ns, key)
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, ns: str, key: str, value: Any) -> None:
        p = self._path(ns, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.replace(p)

    def symbols(self) -> dict[str, str]:
        p = self.root / "symbols.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def add_symbols(self, items: list[tuple[str, str]]) -> dict[str, str]:
        """items: (text, kind). Assigns KIND_n placeholders to unseen texts. Append-only. Returns the full table."""
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / "symbols.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            table = self.symbols()
            counts: dict[str, int] = {}
            for ph in table.values():
                kind = ph.rsplit("_", 1)[0]
                counts[kind] = counts.get(kind, 0) + 1
            for text, kind in items:
                if text not in table:
                    kind = kind.upper()
                    counts[kind] = counts.get(kind, 0) + 1
                    table[text] = f"{kind}_{counts[kind]}"
            tmp = self.root / "symbols.json.tmp"
            tmp.write_text(json.dumps(table, ensure_ascii=False, indent=1))
            tmp.replace(self.root / "symbols.json")
            return table
