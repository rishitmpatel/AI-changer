"""The common formats every adapter converts into and out of.

Memory (memory.json)::

    {
      "schema_version": "1.0",
      "generated_at": "2026-10-20T10:00:00+00:00",
      "source_provider": "chatgpt",
      "categories": {
        "profile":     [Fact, ...],
        "preferences": [Fact, ...],
        "people":      [Fact, ...],
        "projects":    [Fact, ...],
        "topics":      [Fact, ...]
      }
    }

    Fact = {"id": "f-1a2b3c4d", "text": "...", "source_provider": "chatgpt",
            "date": "2026-05-01", "origin": "export_file"}

Chats (chats.json)::

    {"schema_version": "1.0", "source_provider": "claude",
     "conversations": [{"id", "title", "created", "updated", "source_provider",
                        "messages": [{"role": "user|assistant|system", "text", "time"}]}]}

The JSON Schema for both lives in ``ai_switch/schema/*.schema.json``;
:func:`validate_memory` / :func:`validate_chats` implement the same rules without
needing the ``jsonschema`` package.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
CATEGORIES = ("profile", "preferences", "people", "projects", "topics")
ROLES = ("user", "assistant", "system")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?$")


class SchemaError(ValueError):
    """Raised when a document does not match the common format."""


def today() -> str:
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fact_id(text: str) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return "f-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def to_date(value: Any) -> str | None:
    """Best-effort conversion of timestamps found in exports to YYYY-MM-DD."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        try:
            return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return _dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)  # US style 05/31/2026
    if m:
        mo, d, y = (int(g) for g in m.groups())
        try:
            return _dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return _dt.datetime.strptime(text[:20].strip(" ,"), fmt).date().isoformat()
        except ValueError:
            continue
    if re.fullmatch(r"\d{9,13}(\.\d+)?", text):
        return to_date(float(text))
    return None


# --------------------------------------------------------------------------- memory


@dataclass
class Fact:
    text: str
    source_provider: str
    date: str = field(default_factory=today)
    id: str = ""
    origin: str = ""

    def __post_init__(self) -> None:
        self.text = normalize_text(self.text)
        if not self.id:
            self.id = fact_id(self.text)

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "text": self.text, "source_provider": self.source_provider, "date": self.date}
        if self.origin:
            d["origin"] = self.origin
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Fact":
        return cls(text=d["text"], source_provider=d["source_provider"], date=d.get("date") or today(),
                   id=d.get("id", ""), origin=d.get("origin", ""))


@dataclass
class MemoryDoc:
    source_provider: str
    categories: dict[str, list[Fact]] = field(default_factory=lambda: {c: [] for c in CATEGORIES})
    generated_at: str = field(default_factory=now_iso)
    schema_version: str = SCHEMA_VERSION

    # -- building
    def add(self, category: str, fact: Fact) -> bool:
        """Add a fact unless an identical one is already present. Returns True if added."""
        if category not in CATEGORIES:
            raise SchemaError(f"unknown category {category!r}")
        if not fact.text:
            return False
        if any(f.id == fact.id for f in self.all_facts()):
            return False
        self.categories.setdefault(category, []).append(fact)
        return True

    def extend(self, other: "MemoryDoc") -> int:
        added = 0
        for cat, fact in other.iter_facts():
            added += self.add(cat, fact)
        return added

    # -- reading
    def iter_facts(self) -> Iterable[tuple[str, Fact]]:
        for cat in CATEGORIES:
            for f in self.categories.get(cat, []):
                yield cat, f

    def all_facts(self) -> list[Fact]:
        return [f for _, f in self.iter_facts()]

    def __len__(self) -> int:
        return sum(len(v) for v in self.categories.values())

    def counts(self) -> dict[str, int]:
        return {c: len(self.categories.get(c, [])) for c in CATEGORIES}

    def subset(self, ids: set[str]) -> "MemoryDoc":
        doc = MemoryDoc(self.source_provider, generated_at=self.generated_at)
        for cat, f in self.iter_facts():
            if f.id in ids:
                doc.add(cat, f)
        return doc

    # -- (de)serialising
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "source_provider": self.source_provider,
            "categories": {c: [f.to_dict() for f in self.categories.get(c, [])] for c in CATEGORIES},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryDoc":
        validate_memory(d)
        doc = cls(d["source_provider"], generated_at=d.get("generated_at") or now_iso(),
                  schema_version=d.get("schema_version", SCHEMA_VERSION))
        for cat in CATEGORIES:
            for fd in d["categories"].get(cat, []):
                doc.add(cat, Fact.from_dict(fd))
        return doc

    def save(self, path: Path) -> None:
        write_private(path, json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: Path) -> "MemoryDoc":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- chats


@dataclass
class Message:
    role: str
    text: str
    time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "text": self.text}
        if self.time:
            d["time"] = self.time
        return d


@dataclass
class Conversation:
    id: str
    title: str
    source_provider: str
    messages: list[Message] = field(default_factory=list)
    created: str | None = None
    updated: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "source_provider": self.source_provider,
                "created": self.created, "updated": self.updated,
                "messages": [m.to_dict() for m in self.messages]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Conversation":
        return cls(id=str(d["id"]), title=d.get("title") or "Untitled", source_provider=d["source_provider"],
                   created=d.get("created"), updated=d.get("updated"),
                   messages=[Message(m["role"], m["text"], m.get("time")) for m in d.get("messages", [])])


@dataclass
class ChatArchive:
    source_provider: str
    conversations: list[Conversation] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __len__(self) -> int:
        return len(self.conversations)

    def message_count(self) -> int:
        return sum(len(c.messages) for c in self.conversations)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "source_provider": self.source_provider,
                "conversations": [c.to_dict() for c in self.conversations]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatArchive":
        validate_chats(d)
        return cls(d["source_provider"], [Conversation.from_dict(c) for c in d["conversations"]],
                   d.get("schema_version", SCHEMA_VERSION))

    def save(self, path: Path) -> None:
        write_private(path, json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: Path) -> "ChatArchive":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- validation


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)


def validate_memory(d: Any) -> None:
    _need(isinstance(d, dict), "memory document must be a JSON object")
    _need(d.get("schema_version") == SCHEMA_VERSION, f"schema_version must be {SCHEMA_VERSION!r}")
    _need(isinstance(d.get("source_provider"), str) and d["source_provider"], "source_provider is required")
    cats = d.get("categories")
    _need(isinstance(cats, dict), "categories must be an object")
    for key in cats:
        _need(key in CATEGORIES, f"unknown category {key!r}; allowed: {', '.join(CATEGORIES)}")
    for cat, facts in cats.items():
        _need(isinstance(facts, list), f"categories.{cat} must be a list")
        for i, f in enumerate(facts):
            where = f"categories.{cat}[{i}]"
            _need(isinstance(f, dict), f"{where} must be an object")
            for k in ("id", "text", "source_provider", "date"):
                _need(isinstance(f.get(k), str) and f[k].strip() != "", f"{where}.{k} is required")
            _need(bool(_DATE_RE.match(f["date"])), f"{where}.date must be ISO 8601 (YYYY-MM-DD)")


def validate_chats(d: Any) -> None:
    _need(isinstance(d, dict), "chat archive must be a JSON object")
    _need(d.get("schema_version") == SCHEMA_VERSION, f"schema_version must be {SCHEMA_VERSION!r}")
    _need(isinstance(d.get("source_provider"), str), "source_provider is required")
    _need(isinstance(d.get("conversations"), list), "conversations must be a list")
    for i, c in enumerate(d["conversations"]):
        _need(isinstance(c, dict) and "id" in c and isinstance(c.get("messages"), list),
              f"conversations[{i}] needs id and messages")
        for j, m in enumerate(c["messages"]):
            _need(m.get("role") in ROLES, f"conversations[{i}].messages[{j}].role must be one of {ROLES}")
            _need(isinstance(m.get("text"), str), f"conversations[{i}].messages[{j}].text must be a string")


# --------------------------------------------------------------------------- files


def write_private(path: Path, content: str | bytes) -> None:
    """Write a file readable only by the current user (memory content is personal)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(content)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - e.g. some Windows filesystems
        pass
