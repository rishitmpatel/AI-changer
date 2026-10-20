from ai_switch.extract import ASK_MEMORY_PROMPT, BEGIN_MARK, END_MARK, capture_between_markers, categorize, \
    mine_facts_from_chats, parse_memory_text
from ai_switch.schema import ChatArchive, Conversation, Message


def test_prompt_has_markers_and_headings():
    assert BEGIN_MARK in ASK_MEMORY_PROMPT and END_MARK in ASK_MEMORY_PROMPT
    for h in ("## Profile", "## Preferences", "## People", "## Projects", "## Topics"):
        assert h in ASK_MEMORY_PROMPT


def test_parse_ask_ai_reply(fixtures_dir):
    reply = (fixtures_dir / "replies" / "chatgpt_memory_reply.txt").read_text(encoding="utf-8")
    doc = parse_memory_text(reply, "chatgpt", "ask_ai")
    assert doc.counts() == {"profile": 2, "preferences": 2, "people": 1, "projects": 1, "topics": 2}
    texts = [f.text for f in doc.all_facts()]
    assert "Let me know if you'd like me to forget anything." not in texts
    assert not any(t.startswith(("Sure", "```")) for t in texts)
    dated = {f.text: f.date for f in doc.all_facts()}
    assert dated["Prefers concise answers with code first."] == "2026-03-02"
    assert all(f.origin == "ask_ai" and f.source_provider == "chatgpt" for f in doc.all_facts())


def test_capture_without_end_marker():
    assert capture_between_markers(f"intro {BEGIN_MARK}\n- a fact") == "\n- a fact"
    assert capture_between_markers("no markers") == "no markers"


def test_prose_and_bold_headings():
    text = ("**Work context**\nAlex is a nurse in Leeds. Works night shifts.\n\n"
            "**Preferences**\n- Likes short replies\n1. Uses British spelling\n")
    doc = parse_memory_text(text, "claude", "export_file")
    assert [f.text for f in doc.categories["profile"]] == ["Alex is a nurse in Leeds.", "Works night shifts."]
    assert len(doc.categories["preferences"]) == 2


def test_categorize_rules():
    assert categorize("Wife is called Ana") == "people"
    assert categorize("Is building a budgeting app") == "projects"
    assert categorize("Prefers metric units") == "preferences"
    assert categorize("Lives in Berlin") == "profile"
    assert categorize("Interested in astronomy") == "topics"
    assert categorize("Quantum chromodynamics") == "topics"


def test_mine_facts_from_chats_only_uses_user_messages():
    chats = ChatArchive("deepseek", [Conversation("1", "t", "deepseek", [
        Message("user", "My name is Priya Shah and I live in Pune. I prefer Python examples.", "2026-01-01"),
        Message("assistant", "My name is Assistant and I live in the cloud."),
        Message("user", "I'm working on a thesis about monsoon forecasting."),
    ])])
    doc = mine_facts_from_chats(chats, "deepseek")
    texts = [f.text for f in doc.all_facts()]
    assert "User's name is Priya Shah." in texts
    assert "User lives in Pune." in texts
    assert "User prefers Python examples." in texts
    assert "User is working on a thesis about monsoon forecasting." in texts
    assert not any("cloud" in t for t in texts)
    assert all(f.origin == "derived_from_chats" for f in doc.all_facts())
