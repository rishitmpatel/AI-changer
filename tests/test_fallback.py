"""The automatic 1→5 fallback chain."""
import pytest

from ai_switch.adapters import Method, get_adapter
from ai_switch.errors import StepError
from ai_switch.extract import ASK_MEMORY_PROMPT
from ai_switch.schema import Fact, MemoryDoc
from conftest import FakeAI


def test_export_file_wins_when_it_has_memory(make_ctx, fixtures_dir):
    ad = get_adapter("claude", make_ctx(fixtures_dir / "claude"))
    doc = ad.export_memory()
    assert ad.last_method is Method.EXPORT_FILE and len(doc) == 9


def test_no_file_falls_back_to_official_flow(make_ctx, world):
    world["claude"] = FakeAI(memory=["Lives in Austin.", "Prefers metric units."])
    ad = get_adapter("claude", make_ctx())
    doc = ad.export_memory()
    assert ad.last_method is Method.OFFICIAL_FLOW
    assert [a.method for a in ad.attempts] == [Method.EXPORT_FILE, Method.OFFICIAL_FLOW]
    assert {f.text for f in doc.all_facts()} == {"Lives in Austin.", "Prefers metric units."}


def test_broken_selector_falls_through_to_ask_ai(make_ctx, world):
    world["chatgpt"] = FakeAI(memory=["Lives in Austin.", "Partner is Sam."], broken_selectors={"manage_memories"})
    ad = get_adapter("chatgpt", make_ctx())
    doc = ad.export_memory()
    assert ad.last_method is Method.ASK_AI
    assert ASK_MEMORY_PROMPT in world["chatgpt"].sent
    assert len(doc) == 2
    transcript = ad.ctx.console.transcript
    assert "manage_memories" in transcript and "hint:" in transcript


def test_offline_goes_straight_to_clipboard(make_ctx):
    reply = "BEGIN-MEMORY\n## Profile\n- Lives in Austin.\nEND-MEMORY"
    ctx = make_ctx(offline=True)
    ad = get_adapter("gemini", ctx)
    ctx.clipboard.paste = lambda: reply  # the user copied the AI's reply
    doc = ad.export_memory()
    assert ad.last_method is Method.CLIPBOARD
    assert ctx.clipboard.history[0] == ASK_MEMORY_PROMPT
    assert len(doc) == 1 and ctx.console.pauses


def test_clipboard_unchanged_reads_pasted_block(make_ctx):
    ctx = make_ctx(offline=True, blocks=["- Prefers tea over coffee"])
    ad = get_adapter("perplexity", ctx)
    doc = ad.export_memory()
    assert [f.text for f in doc.all_facts()] == ["Prefers tea over coffee"]


def test_chats_only_export_uses_derived_facts_as_last_resort(make_ctx, fixtures_dir):
    ad = get_adapter("chatgpt", make_ctx(fixtures_dir / "chatgpt", offline=True, interactive=False))
    doc = ad.export_memory()
    assert all(f.origin == "derived_from_chats" for f in doc.all_facts())
    assert "derived" in ad.ctx.console.transcript


def test_all_methods_fail_gives_step_error_with_hint(make_ctx, world):
    world["grok"] = FakeAI(logged_in=False)
    ad = get_adapter("grok", make_ctx(interactive=False))
    with pytest.raises(StepError) as exc:
        ad.export_memory()
    err = exc.value
    assert err.step == "Export memory from Grok"
    assert "1 official export file" in str(err) and "3 ask-the-AI prompt" in str(err)
    rendered = err.render()
    assert "What you can do:" in rendered


def test_import_prefers_official_flow_and_splits(make_ctx, world):
    doc = MemoryDoc("chatgpt")
    for i in range(400):
        doc.add("topics", Fact(f"Interesting fact number {i} about hobbies, more hobbies and even more hobbies",
                               "chatgpt"))
    ad = get_adapter("claude", make_ctx())
    res = ad.import_memory(doc)
    assert res.method is Method.OFFICIAL_FLOW and res.parts >= 2
    assert len(world["claude"].memory) == 400
    parts_dir = ad.ctx.out_dir / "import_parts" / "claude-official_flow"
    assert len(list(parts_dir.glob("part-*.txt"))) == res.parts


def test_chatgpt_import_uses_remember_messages(make_ctx, world):
    doc = MemoryDoc("claude")
    doc.add("profile", Fact("Lives in Austin.", "claude"))
    ad = get_adapter("chatgpt", make_ctx())
    res = ad.import_memory(doc)
    assert res.method is Method.ASK_AI
    assert world["chatgpt"].sent[0].startswith("Please remember")
    assert world["chatgpt"].memory == ["Lives in Austin."]


def test_import_clipboard_when_not_logged_in(make_ctx, world):
    world["mistral"] = FakeAI(logged_in=False)
    doc = MemoryDoc("claude")
    doc.add("profile", Fact("Lives in Austin.", "claude"))
    ctx = make_ctx()
    ad = get_adapter("mistral", ctx)
    res = ad.import_memory(doc)
    assert res.method is Method.CLIPBOARD and res.manual
    assert "Lives in Austin." in ctx.clipboard.value
    assert "chat.mistral.ai/memories" in ctx.console.pauses[0]


def test_deepseek_import_is_context_primer(make_ctx, world):
    doc = MemoryDoc("claude")
    doc.add("profile", Fact("Lives in Austin.", "claude"))
    ad = get_adapter("deepseek", make_ctx())
    ad.import_memory(doc)
    assert world["deepseek"].sent[0].startswith("Context about me")


def test_only_methods_restricts_chain(make_ctx, world):
    world["claude"] = FakeAI(memory=["Lives in Austin."])
    ctx = make_ctx(only_methods=(Method.ASK_AI,))
    ad = get_adapter("claude", ctx)
    ad.export_memory()
    assert ad.last_method is Method.ASK_AI


def test_gemini_chat_import_uploads_zip(make_ctx, world, fixtures_dir):
    src = get_adapter("claude", make_ctx(fixtures_dir / "claude")).export_chats()
    ad = get_adapter("gemini", make_ctx())
    res = ad.import_chats(src)
    assert res.method is Method.OFFICIAL_FLOW
    assert world["gemini"].uploads[0].name == "conversations_chatgpt_format.zip"


def test_chat_import_without_support_writes_files(make_ctx, fixtures_dir):
    src = get_adapter("claude", make_ctx(fixtures_dir / "claude")).export_chats()
    ctx = make_ctx()
    ad = get_adapter("copilot", ctx)
    res = ad.import_chats(src)
    assert res.manual
    folder = ctx.out_dir / "chats_for_copilot"
    assert (folder / "chat_history.md").exists() and list((folder / "conversations").glob("*.md"))
