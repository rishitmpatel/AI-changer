"""Every provider's sample export parses into the common format."""
import json
import zipfile

import pytest

from ai_switch.adapters import Context, get_adapter
from ai_switch.errors import MethodFailed, MethodUnavailable
from ai_switch.exports import ExportBundle, parse_chatgpt, parse_generic, write_chatgpt_zip
from ai_switch.schema import validate_chats, validate_memory
from ai_switch.ui import ScriptedConsole


def parse(provider, path):
    ad = get_adapter(provider, Context(console=ScriptedConsole(), export_path=path, offline=True))
    return ad.parsed_export()


EXPECTED = {  # fixture: (provider, conversations, messages, memory facts)
    "chatgpt": ("chatgpt", 2, 8, 0),
    "chatgpt_with_memory": ("chatgpt", 1, 4, 6),
    "claude": ("claude", 1, 2, 9),
    "gemini": ("gemini", 2, 6, 0),
    "grok": ("grok", 1, 2, 0),
    "copilot": ("copilot", 2, 6, 0),
    "perplexity": ("perplexity", 1, 2, 2),
    "meta_ai": ("meta_ai", 1, 2, 2),
    "deepseek": ("deepseek", 1, 4, 0),
    "mistral": ("mistral", 1, 2, 2),
}


@pytest.mark.parametrize("fixture", sorted(EXPECTED))
def test_every_provider_fixture_parses_from_zip(fixture, export_zip):
    provider, n_conv, n_msg, n_mem = EXPECTED[fixture]
    parsed = parse(provider, export_zip(fixture))
    assert len(parsed.chats) == n_conv
    assert parsed.chats.message_count() == n_msg
    assert len(parsed.memory) == n_mem
    validate_chats(parsed.chats.to_dict())
    validate_memory(parsed.memory.to_dict())
    for conv in parsed.chats.conversations:
        assert conv.messages[0].role == "user"


@pytest.mark.parametrize("fixture", ["claude", "chatgpt", "gemini"])
def test_folder_and_zip_give_same_result(fixture, export_zip, fixtures_dir):
    provider = EXPECTED[fixture][0]
    a = parse(provider, export_zip(fixture))
    b = parse(provider, fixtures_dir / fixture)
    assert a.chats.to_dict() == b.chats.to_dict()
    assert [f.text for f in a.memory.all_facts()] == [f.text for f in b.memory.all_facts()]


def test_chatgpt_follows_current_branch_and_hides_system(fixtures_dir):
    parsed = parse("chatgpt", fixtures_dir / "chatgpt")
    texts = [m.text for c in parsed.chats.conversations for m in c.messages]
    assert "(old regenerated answer)" not in texts
    assert all(m.role != "system" for c in parsed.chats.conversations for m in c.messages)
    assert parsed.chats.conversations[0].title == "Trip planning Lisbon"
    assert parsed.chats.conversations[0].created == "2025-05-11"


def test_chatgpt_memories_file_dates(fixtures_dir):
    mem = parse("chatgpt", fixtures_dir / "chatgpt_with_memory").memory
    dates = {f.text: f.date for f in mem.all_facts()}
    assert dates["Prefers concise answers with code first."] == "2026-03-02"
    assert mem.counts()["people"] == 1 and mem.counts()["projects"] == 1


def test_claude_memory_and_thinking(fixtures_dir):
    parsed = parse("claude", fixtures_dir / "claude")
    reply = parsed.chats.conversations[0].messages[1].text
    assert "internal" not in reply and reply.startswith("Here is a draft")
    texts = [f.text for f in parsed.memory.all_facts()]
    assert "User's name is Alex Rivera." in texts
    assert any("walkdir" in f.text for f in parsed.memory.categories["projects"])
    assert "Uses metric units" in [f.text for f in parsed.memory.categories["preferences"]]
    assert any("partner Sam" in f.text for f in parsed.memory.categories["people"])
    # the email in users.json must not become a fact
    assert not any("@" in t for t in texts)


def test_gemini_takeout_filters_non_gemini_activity(fixtures_dir):
    chats = parse("gemini", fixtures_dir / "gemini").chats
    texts = [m.text for c in chats.conversations for m in c.messages]
    assert not any("weather austin" in t for t in texts)
    assert not any(t.startswith("Used") for t in texts)
    assert "Try the Barton Creek Greenbelt & Hamilton Pool…" in texts


def test_grok_mongo_dates(fixtures_dir):
    conv = parse("grok", fixtures_dir / "grok").chats.conversations[0]
    assert conv.id == "grok-conv-01" and conv.title == "Startup name ideas"
    assert [m.role for m in conv.messages] == ["user", "assistant"]
    assert conv.messages[0].time == "2025-06-01"


def test_copilot_csv(fixtures_dir):
    chats = parse("copilot", fixtures_dir / "copilot").chats
    assert [c.title for c in chats.conversations] == ["Kitchen renovation", "Email draft"]
    assert chats.conversations[0].messages[1].role == "assistant"


def test_meta_mojibake_repaired(fixtures_dir):
    parsed = parse("meta_ai", fixtures_dir / "meta_ai")
    conv = parsed.chats.conversations[0]
    assert conv.title == "Café recommendations"
    assert "café near Zilker Park — I work" in conv.messages[0].text
    assert "Loves café culture and quiet places to work" in [f.text for f in parsed.memory.all_facts()]


def test_deepseek_fragments_skip_thinking(fixtures_dir):
    conv = parse("deepseek", fixtures_dir / "deepseek").chats.conversations[0]
    assert all("internal reasoning" not in m.text for m in conv.messages)
    assert [m.role for m in conv.messages] == ["user", "assistant", "user", "assistant"]


def test_write_chatgpt_zip_roundtrip(fixtures_dir, tmp_path):
    chats = parse("claude", fixtures_dir / "claude").chats
    z = write_chatgpt_zip(chats, tmp_path / "out.zip")
    with zipfile.ZipFile(z) as zf:
        assert zf.namelist() == ["conversations.json"]
    again = parse_chatgpt(ExportBundle.open(z)).chats
    assert [m.text for m in again.conversations[0].messages] == [m.text for m in chats.conversations[0].messages]


def test_errors_are_clear(tmp_path):
    with pytest.raises(MethodUnavailable) as e:
        ExportBundle.open(tmp_path / "nope.zip")
    assert "--export" in e.value.hint
    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"hello": "world"}))
    with pytest.raises(MethodFailed, match="found no chats or memory"):
        parse_generic(ExportBundle.open(junk), "grok")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "conversations.json").write_text("[{")
    with pytest.raises(MethodFailed, match="not valid JSON"):
        parse_chatgpt(ExportBundle.open(broken))
    with pytest.raises(MethodFailed, match="ChatGPT export"):
        parse_chatgpt(ExportBundle.open(junk))
