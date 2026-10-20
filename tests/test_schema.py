import json
import sys
from pathlib import Path

import pytest

from ai_switch.schema import (CATEGORIES, ChatArchive, Conversation, Fact, MemoryDoc, Message, SchemaError,
                              fact_id, to_date, validate_chats, validate_memory)

SCHEMA_DIR = Path(__file__).parents[1] / "ai_switch" / "schema"


def sample_doc() -> MemoryDoc:
    doc = MemoryDoc("chatgpt")
    doc.add("profile", Fact("Lives in Austin.", "chatgpt", "2026-01-02"))
    doc.add("preferences", Fact("Prefers concise answers.", "chatgpt", "2026-01-03", origin="ask_ai"))
    return doc


def test_roundtrip(tmp_path):
    doc = sample_doc()
    path = tmp_path / "memory.json"
    doc.save(path)
    loaded = MemoryDoc.load(path)
    assert loaded.to_dict() == doc.to_dict()
    assert set(json.loads(path.read_text(encoding="utf-8"))["categories"]) == set(CATEGORIES)
    if sys.platform != "win32":
        assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_dedupe_by_normalised_text():
    doc = sample_doc()
    assert not doc.add("topics", Fact("lives in   AUSTIN", "claude"))
    assert len(doc) == 2
    assert fact_id("Lives in Austin.") == fact_id("lives in austin")


def test_validation_errors():
    good = sample_doc().to_dict()
    validate_memory(good)
    bad = json.loads(json.dumps(good))
    bad["categories"]["hobbies"] = []
    with pytest.raises(SchemaError, match="unknown category"):
        validate_memory(bad)
    bad = json.loads(json.dumps(good))
    bad["categories"]["profile"][0]["date"] = "yesterday"
    with pytest.raises(SchemaError, match="ISO 8601"):
        validate_memory(bad)
    bad = json.loads(json.dumps(good))
    del bad["categories"]["profile"][0]["source_provider"]
    with pytest.raises(SchemaError, match="source_provider"):
        validate_memory(bad)


def test_chats_roundtrip(tmp_path):
    arch = ChatArchive("claude", [Conversation("c1", "T", "claude", [Message("user", "hi"), Message("assistant", "yo")])])
    arch.save(tmp_path / "c.json")
    assert ChatArchive.load(tmp_path / "c.json").message_count() == 2
    with pytest.raises(SchemaError):
        validate_chats({"schema_version": "1.0", "source_provider": "x",
                        "conversations": [{"id": "1", "messages": [{"role": "robot", "text": ""}]}]})


@pytest.mark.parametrize("value,expected", [
    (1747000000, "2025-05-11"), (1747000000000, "2025-05-11"), ("2026-02-14T09:12:00Z", "2026-02-14"),
    ("05/31/2026", "2026-05-31"), ("Mar 2, 2026", "2026-03-02"), ("1747000000", "2025-05-11"),
    ("", None), ("not a date", None),
])
def test_to_date(value, expected):
    assert to_date(value) == expected


def test_json_schema_files_agree_with_validator():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMA_DIR / "memory.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(sample_doc().to_dict(), schema)
    chats = json.loads((SCHEMA_DIR / "chats.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(ChatArchive("x", [Conversation("1", "t", "x", [Message("user", "a")])]).to_dict(), chats)


def test_schema_files_are_valid_json():
    for f in SCHEMA_DIR.glob("*.json"):
        assert json.loads(f.read_text(encoding="utf-8"))["type"] == "object"
