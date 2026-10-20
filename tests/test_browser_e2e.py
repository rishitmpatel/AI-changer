"""Real Chromium + Playwright against a local fake assistant site (no real accounts).

Exercises BrowserSession: persistent profile, strict network (third-party script blocked),
selectors with alternatives, streaming-reply detection, YAML flows for export and import.
"""
import functools
import http.server
import threading

import pytest

pytest.importorskip("playwright")

from ai_switch import migrate  # noqa: E402
from ai_switch.adapters import BaseAdapter, Context, Method, _REGISTRY, register  # noqa: E402
from ai_switch.browser import BrowserSession  # noqa: E402
from ai_switch.clipboard import MemoryClipboard  # noqa: E402
from ai_switch.ui import ScriptedConsole  # noqa: E402

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def site():
    from pathlib import Path

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(Path(__file__).parent / "fake_site"))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def fake_config(base, seed):
    return {
        "display_name": "FakeAI",
        "urls": {"home": f"{base}/index.html{'?seed=1' if seed else ''}", "new_chat": f"{base}/index.html",
                 "memory_page": f"{base}/memory.html"},
        "selectors": {
            "logged_in": ["#does-not-exist", "#me"],  # first alternative is stale on purpose
            "chat_input": ["#input"], "send_button": ["#send"], "stop_button": ["#stop"],
            "assistant_message": [".msg.assistant"],
            "memory_item": ["#list li.item"], "start_import": ["text=Start import"],
            "import_textbox": ["#import-box"], "add_to_memory": ["button:has-text('Add to memory')"],
        },
        "flows": {
            "export_memory": [{"goto": "memory_page"}, {"collect": "memory_item"}],
            "import_memory": [{"goto": "memory_page"}, {"click": "start_import"},
                              {"fill": {"into": "import_textbox", "text": "{part}"}}, {"click": "add_to_memory"},
                              {"wait": 0.3}],
        },
        "import": {"official_flow": {"style": "block", "limit": 5000}},
        "manual_steps": {"import_memory": "Paste it."},
    }


@pytest.fixture
def providers(site, tmp_path, chromium_path, monkeypatch):
    if not chromium_path:
        pytest.skip("no Chromium available")
    monkeypatch.setenv("AI_SWITCH_BROWSER_PATH", chromium_path)

    class SourceAI(BaseAdapter):
        id, name = "fake_src", "FakeSource"
        DEFAULT_CONFIG = fake_config(site, seed=True)
        export_memory_methods = (Method.ASK_AI, Method.CLIPBOARD)

    class TargetAI(BaseAdapter):
        id, name = "fake_dst", "FakeTarget"
        DEFAULT_CONFIG = fake_config(site.replace("127.0.0.1", "localhost"), seed=False)

    register(SourceAI)
    register(TargetAI)
    profiles = {"fake_src": tmp_path / "p-src", "fake_dst": tmp_path / "p-dst"}

    def factory(config):
        return BrowserSession(config, headless=True, profile=profiles[config.provider], timeout_s=30)

    ctx = Context(console=ScriptedConsole(answers={"REMOVE": "none"}), clipboard=MemoryClipboard(),
                  browser_factory=factory, out_dir=tmp_path / "out")
    yield ctx
    _REGISTRY.pop("fake_src", None)
    _REGISTRY.pop("fake_dst", None)


def test_real_browser_move(providers):
    ctx = providers
    res = migrate.move(ctx, "fake_src", "fake_dst", retry=False)
    assert res.export_method == "ask_ai"
    assert len(res.memory) == 3
    assert res.imported.method is Method.OFFICIAL_FLOW
    assert res.report.coverage == 1.0, (res.out_dir / "verify_report.md").read_text(encoding="utf-8")
    log = (res.out_dir / "run.log").read_text(encoding="utf-8")
    assert "tracker.third-party.example" in log  # strict network blocked the third-party script
    assert "Austin" not in log and "Austin" not in ctx.console.transcript


def test_real_browser_official_export_flow(providers):
    from ai_switch.adapters import get_adapter

    ctx = providers
    with BrowserSession(get_adapter("fake_src", ctx).config, headless=True,
                        profile=ctx.out_dir.parent / "p-src", timeout_s=30) as b:
        b.goto("home")  # seed localStorage
    ad = get_adapter("fake_src", ctx)
    ad.export_memory_methods = (Method.OFFICIAL_FLOW,)
    doc = ad.export_memory()
    assert {f.text for f in doc.all_facts()} == {"Lives in Austin.", "Prefers concise answers with code first.",
                                                 "Partner is named Sam."}
