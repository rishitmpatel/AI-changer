"""End-to-end move flows with the fake browser. Also checks memory never reaches the terminal or log."""
import json

from ai_switch import migrate
from ai_switch.adapters import Method
from conftest import FakeAI

SECRET_BITS = ["Northwind", "tidyfs", "Miso", "bouldering", "alex.rivera", "415 555", "Austin"]


def test_move_chatgpt_to_claude_full_flow(make_ctx, world, fixtures_dir):
    ctx = make_ctx(fixtures_dir / "chatgpt_with_memory", answers={"REMOVE": "all"})
    res = migrate.move(ctx, "chatgpt", "claude", with_chats=True, retry=False)
    out = res.out_dir
    for name in ("memory.json", "memory.md", "chats.json", "chats.md", "privacy_review.md", "verify_report.md",
                 "run.log"):
        assert (out / name).exists(), name
    mem = json.loads((out / "memory.json").read_text())
    texts = [f["text"] for facts in mem["categories"].values() for f in facts]
    assert "Phone number is [removed phone number] for SMS reminders." in texts
    assert res.imported.method is Method.OFFICIAL_FLOW
    assert res.chats_imported.method is Method.OFFICIAL_FLOW
    assert world["claude"].uploads
    assert res.report.coverage == 1.0
    # privacy: no memory content in terminal output or run.log
    transcript = ctx.console.transcript + (out / "run.log").read_text()
    for bit in SECRET_BITS:
        assert bit not in transcript, bit
    assert "+1 *" in ctx.console.transcript  # masked identifier shown


def test_move_reports_missing_and_retries(make_ctx, world, fixtures_dir):
    class ForgetfulAI(FakeAI):
        def respond(self, text):
            if "what do you know about me" in text.lower():
                return "You prefer concise answers with code first."
            return super().respond(text)

    world["gemini"] = ForgetfulAI()
    ctx = make_ctx(fixtures_dir / "chatgpt_with_memory")
    res = migrate.move(ctx, "chatgpt", "gemini", redact="none", retry=True)
    assert res.report.missing and res.report.covered
    assert "re-sent" in ctx.console.transcript
    report = (res.out_dir / "verify_report.md").read_text()
    assert "## Missing" in report


def test_move_offline_is_fully_manual(make_ctx, fixtures_dir):
    ctx = make_ctx(fixtures_dir / "claude", offline=True, answers={"REMOVE": "none"})
    answer = "You are a staff backend engineer at Northwind Labs."
    ctx.clipboard.paste = lambda: answer
    res = migrate.move(ctx, "claude", "grok")
    assert res.imported.manual and res.imported.method is Method.CLIPBOARD
    assert res.report.covered and res.report.missing
    assert len(ctx.console.pauses) >= 2  # paste part(s) + verify question


def test_export_only_and_import_only(make_ctx, world, fixtures_dir):
    ctx = make_ctx(fixtures_dir / "perplexity")
    backup = migrate.export_only(ctx, "perplexity")
    assert (backup.out_dir / "memory.md").read_text().startswith("# Memory exported from perplexity")
    res = migrate.import_only(make_ctx(), "mistral", backup.out_dir / "memory.json", redact="all", verify=False)
    assert res.imported.method is Method.OFFICIAL_FLOW
    assert "Commutes 10 km by bike to work." in world["mistral"].memory


def test_same_provider_rejected(make_ctx):
    import pytest

    from ai_switch.errors import StepError

    with pytest.raises(StepError, match="same provider"):
        migrate.move(make_ctx(), "openai", "chatgpt")
