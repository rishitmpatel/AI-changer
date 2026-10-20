"""Shared fixtures: zipped sample exports, an isolated ~/.ai-switch, and a fake browser
that behaves like an assistant with memory (so fallback chains run without real logins)."""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ai_switch.adapters import Context
from ai_switch.clipboard import MemoryClipboard
from ai_switch.errors import MethodFailed, MethodUnavailable, NotLoggedIn
from ai_switch.extract import BEGIN_MARK, END_MARK
from ai_switch.ui import ScriptedConsole

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "ai-switch-home"
    monkeypatch.setenv("AI_SWITCH_HOME", str(home))
    return home


def zip_folder(folder: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(folder).as_posix())
    return dest


@pytest.fixture
def export_zip(tmp_path):
    """export_zip('claude') -> path to a zip built from tests/fixtures/claude/."""

    def make(name: str) -> Path:
        return zip_folder(FIXTURES / name, tmp_path / f"{name}-export.zip")

    return make


# --------------------------------------------------------------------------- fake browser


@dataclass
class FakeAI:
    """State of one fake provider account."""

    memory: list[str] = field(default_factory=list)
    logged_in: bool = True
    broken_selectors: set[str] = field(default_factory=set)
    sent: list[str] = field(default_factory=list)  # every chat message received
    uploads: list[Path] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)  # settings text boxes
    replies: list[str] = field(default_factory=list)

    def respond(self, text: str) -> str:
        self.sent.append(text)
        if BEGIN_MARK in text:
            body = "\n".join(f"- {m}" for m in self.memory)
            return f"Sure!\n```\n{BEGIN_MARK}\n{body}\n{END_MARK}\n```"
        if text.lower().startswith(("please remember", "please also remember", "context about me")):
            for line in text.splitlines():
                if line.startswith("- "):
                    self.memory.append(line[2:].strip())
            return "Memory updated."
        if "what do you know about me" in text.lower():
            return "Here's what I know:\n" + "\n".join(f"- {m}" for m in self.memory)
        return "OK."


class FakeElement:
    def __init__(self, browser: "FakeBrowser", key: str) -> None:
        self.browser, self.key = browser, key

    def input_value(self) -> str:
        return self.browser.ai.fields.get(self.key, "")

    def click(self) -> None:
        self.browser.click(self.key)


class _Keyboard:
    def press(self, key: str) -> None:
        pass

    def insert_text(self, text: str) -> None:
        pass


class _Page:
    keyboard = _Keyboard()

    def wait_for_timeout(self, ms: int) -> None:
        pass


SUBMIT_KEYS = {"add_to_memory", "import_memory_submit", "import_submit", "saved_info_submit", "memory_submit",
               "memory_edit_submit", "save_button"}
MEMORY_LIST_KEYS = {"memory_item", "saved_info_item", "memory_text"}


class FakeBrowser:
    """Implements the BrowserSession surface used by adapters, backed by a FakeAI."""

    def __init__(self, config, ai: FakeAI) -> None:
        self.config, self.ai = config, ai
        self.page = _Page()
        self.visited: list[str] = []
        self.last_fill = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def goto(self, key: str) -> None:
        if not self.config.url(key):
            raise MethodUnavailable(f"no '{key}' URL configured")
        self.visited.append(key)

    def find(self, key: str, timeout_ms=None, required: bool = True):
        if key in self.ai.broken_selectors or not self.config.selector(key):
            if not required:
                return None
            raise MethodFailed(f"could not find '{key}'", hint=f"Edit selectors.{key}")
        return FakeElement(self, key)

    def click(self, key: str, required: bool = True) -> bool:
        if self.find(key, required=required) is None:
            return False
        if key in SUBMIT_KEYS and self.last_fill:
            for line in self.last_fill.splitlines():
                line = re.sub(r"^[-*]\s+", "", line.strip())
                if line and not line.startswith(("#", "Here is what", "(continued)")):
                    self.ai.memory.append(line)
            self.last_fill = ""
        return True

    def fill(self, key: str, text: str) -> None:
        self.find(key)
        self.ai.fields[key] = text
        self.last_fill = text

    def texts(self, key: str) -> list[str]:
        if key in self.ai.broken_selectors:
            return []
        if key in MEMORY_LIST_KEYS:
            return list(self.ai.memory)
        if key == "assistant_message":
            return list(self.ai.replies)
        return []

    def upload(self, key: str, path: Path) -> None:
        self.find(key)
        self.ai.uploads.append(Path(path))

    def ensure_logged_in(self, timeout_ms: int = 0) -> None:
        if not self.ai.logged_in:
            raise NotLoggedIn("not signed in", hint=f"Run: ai-switch login {self.config.provider}")

    def send_chat(self, text: str, new_chat: bool = True) -> None:
        self.find("chat_input")
        self.ai.replies.append(self.ai.respond(text))

    def wait_reply(self, timeout_s: float = 0, settle_s: float = 0, until=None, before_count: int = 0) -> str:
        if "assistant_message" in self.ai.broken_selectors:
            raise MethodFailed("no reply", hint="fix assistant_message")
        return self.ai.replies[-1]

    def ask(self, prompt: str, until=None, timeout_s: float = 0) -> str:
        self.send_chat(prompt)
        return self.wait_reply()


@pytest.fixture
def world():
    """Map provider id -> FakeAI; providers not listed get an empty logged-in account."""
    return {}


@pytest.fixture
def make_ctx(tmp_path, world):
    def make(export: Path | None = None, *, offline: bool = False, answers=None, blocks=None,
             interactive: bool = True, **kw) -> Context:
        console = ScriptedConsole(answers=answers or {}, blocks=blocks)
        console.interactive = interactive

        def factory(config):
            if offline:
                raise MethodUnavailable("offline")
            return FakeBrowser(config, world.setdefault(config.provider, FakeAI()))

        return Context(console=console, clipboard=MemoryClipboard(), browser_factory=factory,
                       export_path=export, out_dir=tmp_path / "out", offline=offline, **kw)

    return make


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def _playwright_chromium() -> str | None:
    """The Chromium that `python -m playwright install chromium` downloaded (any OS)."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return p.chromium.executable_path
    except Exception:
        return None


@pytest.fixture(scope="session")
def chromium_path() -> str | None:
    candidates = (os.environ.get("AI_SWITCH_BROWSER_PATH"), "/opt/pw-browsers/chromium", _playwright_chromium(),
                  shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"))
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    if os.environ.get("CI"):  # never let CI pass by silently skipping the real-browser tests
        pytest.fail("no Chromium found; run: python -m playwright install chromium")
    return None
