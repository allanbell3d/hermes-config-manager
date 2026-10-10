"""'My defaults': model chains the user saved (head + fallbacks, each provider / model / effort).

One JSON file in the user's config folder.  Export writes the same format; import adds the
defaults of another file (a clashing name gets a number appended).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT = "hermes-config-manager-defaults"
VERSION = 1


@dataclass
class ChainDefault:
    name: str
    chain: list[list[Any]]           # [[provider, model, effort or None], ...]

    def to_json(self) -> dict:
        return {"name": self.name, "chain": [[r[0], r[1], r[2]] for r in self.chain]}

    @classmethod
    def from_json(cls, raw: Any) -> "ChainDefault | None":
        if not isinstance(raw, dict) or not isinstance(raw.get("chain"), list):
            return None
        rows = []
        for r in raw["chain"]:
            if isinstance(r, (list, tuple)) and len(r) >= 2 and r[0] and r[1]:
                rows.append([str(r[0]), str(r[1]), (str(r[2]) if len(r) > 2 and r[2] else None)])
        if not rows:
            return None
        return cls(str(raw.get("name") or "Unnamed"), rows)


def parse(text: str) -> list[ChainDefault]:
    raw = json.loads(text)
    items = raw.get("defaults") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("not a defaults file")
    return [d for d in (ChainDefault.from_json(x) for x in items) if d is not None]


class DefaultsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.items: list[ChainDefault] = []
        try:
            self.items = parse(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"format": FORMAT, "version": VERSION, "defaults": [d.to_json() for d in self.items]}
        fd, tmp = tempfile.mkstemp(prefix=".defaults.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def add(self, name: str, chain: list[list[Any]]) -> ChainDefault:
        d = ChainDefault(self._unique(name.strip() or f"My default {len(self.items) + 1}"),
                         [list(r) for r in chain])
        self.items.append(d)
        self.save()
        return d

    def replace(self, index: int, name: str, chain: list[list[Any]]) -> None:
        old = self.items[index]
        self.items[index] = ChainDefault(name.strip() or old.name, [list(r) for r in chain])
        self.save()

    def delete(self, index: int) -> None:
        del self.items[index]
        self.save()

    def export(self, target: Path) -> int:
        payload = {"format": FORMAT, "version": VERSION, "defaults": [d.to_json() for d in self.items]}
        Path(target).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        return len(self.items)

    def import_file(self, source: Path) -> int:
        added = parse(Path(source).read_text(encoding="utf-8"))
        for d in added:
            d.name = self._unique(d.name)
            self.items.append(d)
        if added:
            self.save()
        return len(added)

    def _unique(self, name: str) -> str:
        names = {d.name for d in self.items}
        if name not in names:
            return name
        i = 2
        while f"{name} ({i})" in names:
            i += 1
        return f"{name} ({i})"
