"""Read-only view of one project's memory. The single reader every command uses.

  features/<slug>/feature.json     structured feature data (see features.py)
  features/<slug>/decisions.json   decisions agreed with the user (written by consolidation)
  features/<slug>/*.md             natural-language docs
  patches/<timestamp>.md           `---` frontmatter with `key: value` / `key: [a, b]`
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from hippo.features import Feature, load_all

BULLET = re.compile(r"^\s*[-*]\s+(?P<text>.+)$")


@dataclass
class Patch:
    path: Path
    meta: dict = field(default_factory=dict)

    @property
    def features(self) -> list[str]:
        f = self.meta.get("features", [])
        return f if isinstance(f, list) else [f]

    @property
    def created_at(self) -> str:
        return str(self.meta.get("created_at") or self.path.stem)


def bullets(body: str) -> list[str]:
    return [m["text"].strip() for line in body.splitlines() if (m := BULLET.match(line))]


def frontmatter(text: str) -> dict:
    """Minimal `---` block reader: `key: value` and `key: [a, b]` only."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            out[key.strip()] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            out[key.strip()] = value.strip("'\"")
    return {}  # no closing ---


class Memory:
    def __init__(self, root: Path):
        self.root = root

    def features(self) -> tuple[list[Feature], list[str]]:
        """Features sorted by slug, plus a line for each unreadable feature.json."""
        return load_all(self.root)

    def doc(self, slug: str, name: str) -> str:
        f = self.root / "features" / slug / name
        return f.read_text() if f.exists() else ""

    def decisions(self, slug: str) -> list[dict]:
        f = self.root / "features" / slug / "decisions.json"
        return json.loads(f.read_text()) if f.exists() else []

    def patches(self) -> list[Patch]:
        """Unmerged patches, oldest first (filenames are sortable timestamps)."""
        d = self.root / "patches"
        if not d.is_dir():
            return []
        return [Patch(p, frontmatter(p.read_text())) for p in sorted(d.glob("*.md"))]

    def needs_review(self) -> list[dict]:
        """Open items in needs-review.json (see review.py)."""
        from hippo.review import open_items
        return open_items(self.root)
