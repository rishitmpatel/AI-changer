"""Settings and move history, stored as small JSON files in the app data folder.

History keeps only metadata (date, from, to, counts, folder) — never memory content.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ..config import default_output_dir, home_dir
from ..fixes import DEFAULT_SOURCE
from ..schema import write_private

BROWSER_CHOICES = ("auto", "edge", "chrome")


@dataclass
class Settings:
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    browser: str = "auto"
    language: str = "en"
    show_browser: bool = False
    fixes_source: str = DEFAULT_SOURCE
    fixes_token: str = ""

    @classmethod
    def path(cls) -> Path:
        return home_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(cls.path().read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known})
        if s.browser not in BROWSER_CHOICES:
            s.browser = "auto"
        return s

    def update(self, changes: dict[str, Any]) -> "Settings":
        for key, value in changes.items():
            if key == "browser" and value not in BROWSER_CHOICES:
                raise ValueError(f"browser must be one of {BROWSER_CHOICES}")
            if key == "language" and value != "en":
                raise ValueError("only English is available for now")
            if key == "output_dir" and not str(value).strip():
                raise ValueError("output folder can't be empty")
            if key in {f.name for f in fields(self)}:
                setattr(self, key, type(getattr(self, key))(value))
        self.save()
        return self

    def save(self) -> None:
        write_private(self.path(), json.dumps(asdict(self), indent=2))

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["fixes_token"] = "set" if self.fixes_token else ""
        return d


class History:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or home_dir() / "history.json"

    def all(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return []

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry = {"date": _dt.datetime.now().isoformat(timespec="seconds"), **entry}
        items = [entry] + self.all()
        write_private(self.path, json.dumps(items[:200], indent=2))
        return entry
