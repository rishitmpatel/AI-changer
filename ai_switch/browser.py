"""Playwright wrapper: one persistent Chrome profile, selectors from selectors.yaml, strict network.

Only this module imports Playwright, and only when a browser method actually runs,
so export-file and clipboard methods work without it installed.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import ProviderConfig, profile_dir
from .errors import MethodFailed, MethodUnavailable, NotLoggedIn

log = logging.getLogger("ai_switch.browser")


class BrowserSession:
    """A signed-in browser for one provider. Use as a context manager."""

    def __init__(self, config: ProviderConfig, *, headless: bool = False, strict_network: bool = True,
                 profile: Path | None = None, timeout_s: float = 30.0) -> None:
        self.config = config
        self.headless = headless
        self.strict_network = strict_network
        self.profile = profile or profile_dir()
        self.timeout_ms = int(timeout_s * 1000)
        self.blocked_hosts: set[str] = set()
        self._pw = None
        self._ctx = None
        self.page = None

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise MethodUnavailable("Playwright is not installed",
                                    hint='pip install "ai-switch[browser]" && python -m playwright install chrome') from exc
        self.profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs: dict[str, Any] = {"headless": self.headless, "viewport": {"width": 1280, "height": 900},
                                  "accept_downloads": True}
        exe = os.environ.get("AI_SWITCH_BROWSER_PATH")
        channel = os.environ.get("AI_SWITCH_BROWSER_CHANNEL", "chrome")
        try:
            if exe:
                self._ctx = self._pw.chromium.launch_persistent_context(str(self.profile), executable_path=exe, **kwargs)
            else:
                try:
                    self._ctx = self._pw.chromium.launch_persistent_context(str(self.profile), channel=channel, **kwargs)
                except Exception:
                    self._ctx = self._pw.chromium.launch_persistent_context(str(self.profile), **kwargs)
        except Exception as exc:
            self._pw.stop()
            msg = str(exc).splitlines()[0]
            hint = "Run: python -m playwright install chrome  (or set AI_SWITCH_BROWSER_PATH to your Chrome)."
            if "lock" in msg.lower() or "in use" in msg.lower():
                hint = "Close other ai-switch browser windows; the profile can only be open once."
            raise MethodUnavailable(f"could not start the browser: {msg}", hint=hint) from exc
        if self.strict_network:
            self._ctx.route("**/*", self._route)
        self._ctx.set_default_timeout(self.timeout_ms)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.blocked_hosts:
            log.info("strict network blocked %d third-party host(s): %s", len(self.blocked_hosts),
                     ", ".join(sorted(self.blocked_hosts)))
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _route(self, route: Any) -> None:
        url = route.request.url
        parsed = urlparse(url)
        if parsed.scheme in ("data", "blob", "file", "about", "chrome-extension") or self.config.host_allowed(parsed.hostname or ""):
            route.continue_()
        else:
            self.blocked_hosts.add(parsed.hostname or "?")
            route.abort()

    # ------------------------------------------------------------------ helpers
    def goto(self, url_key: str) -> None:
        url = self.config.url(url_key)
        if not url:
            raise MethodUnavailable(f"no '{url_key}' URL configured for {self.config.provider}",
                                    hint=f"Add urls.{url_key} to selectors/{self.config.provider}.yaml")
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            raise MethodFailed(f"could not open {url}: {str(exc).splitlines()[0]}",
                               hint="Check your connection. If pages stay blank, run with --no-strict-network "
                                    "or add the missing domain to allowed_domains in selectors.yaml.") from exc

    def find(self, key: str, timeout_ms: int | None = None, required: bool = True) -> Any:
        """Return the first visible element matching any alternative for selector ``key``."""
        alternatives = self.config.selector(key)
        if not alternatives:
            if not required:
                return None
            raise MethodUnavailable(f"selector '{key}' is not configured for {self.config.provider}",
                                    hint=f"Add selectors.{key} to selectors/{self.config.provider}.yaml")
        deadline = time.monotonic() + (timeout_ms if timeout_ms is not None else self.timeout_ms) / 1000
        while True:
            for sel in alternatives:
                try:
                    loc = self.page.locator(sel)
                    if loc.count() and loc.first.is_visible():
                        return loc.first
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                break
            self.page.wait_for_timeout(250)
        if not required:
            return None
        raise MethodFailed(
            f"could not find '{key}' on {self.config.display_name} (tried {len(alternatives)} selector(s))",
            hint=f"The site layout probably changed. Edit selectors.{key} in "
                 f"~/.ai-switch/selectors/{self.config.provider}.yaml (see README → Fixing a broken selector).")

    def click(self, key: str, required: bool = True) -> bool:
        el = self.find(key, required=required)
        if el is None:
            return False
        el.click()
        return True

    def fill(self, key: str, text: str) -> None:
        el = self.find(key)
        el.click()
        try:
            el.fill(text)
        except Exception:  # contenteditable editors (ProseMirror etc.)
            self.page.keyboard.insert_text(text)

    def texts(self, key: str) -> list[str]:
        alternatives = self.config.selector(key)
        for sel in alternatives:
            try:
                loc = self.page.locator(sel)
                if loc.count():
                    return [t.strip() for t in loc.all_inner_texts() if t.strip()]
            except Exception:
                continue
        return []

    def upload(self, key: str, path: Path) -> None:
        alternatives = self.config.selector(key)
        for sel in alternatives:
            loc = self.page.locator(sel)
            if loc.count():
                loc.first.set_input_files(str(path))
                return
        raise MethodFailed(f"no file input '{key}' found on {self.config.display_name}",
                           hint=f"Edit selectors.{key} in ~/.ai-switch/selectors/{self.config.provider}.yaml")

    # ------------------------------------------------------------------ login + chat
    def ensure_logged_in(self, timeout_ms: int = 15000) -> None:
        self.goto("home")
        if self.find("logged_in", timeout_ms=timeout_ms, required=False) is None:
            raise NotLoggedIn(f"not signed in to {self.config.display_name} in the ai-switch browser profile",
                              hint=f"Run: ai-switch login {self.config.provider}")

    def send_chat(self, text: str, new_chat: bool = True) -> None:
        if new_chat:
            self.goto("new_chat" if self.config.url("new_chat") else "home")
        self.fill("chat_input", text)
        if not self.click("send_button", required=False):
            self.page.keyboard.press("Enter")

    def wait_reply(self, timeout_s: float = 180, settle_s: float = 3.0, until: str | None = None,
                   before_count: int = 0) -> str:
        """Wait for the newest assistant message to stop changing; return its text."""
        deadline = time.monotonic() + timeout_s
        last, stable_since = "", None
        while time.monotonic() < deadline:
            replies = self.texts("assistant_message")
            current = replies[-1] if len(replies) > before_count else ""
            generating = self.find("stop_button", timeout_ms=0, required=False) is not None
            if current and current == last and not generating:
                stable_since = stable_since or time.monotonic()
                if (until and until in current) or time.monotonic() - stable_since >= settle_s:
                    return current
            else:
                stable_since = None
            last = current
            self.page.wait_for_timeout(500)
        if last:
            return last
        raise MethodFailed(f"no reply from {self.config.display_name} within {int(timeout_s)}s",
                           hint="The assistant may be slow or the 'assistant_message' selector is outdated. "
                                "Retry, or fix the selector in selectors.yaml.")

    def ask(self, prompt: str, until: str | None = None, timeout_s: float = 180) -> str:
        self.send_chat(prompt, new_chat=True)
        return self.wait_reply(timeout_s=timeout_s, until=until)
