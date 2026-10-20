"""Playwright wrapper: selectors from selectors.yaml, self-healing lookups, strict network.

Two ways to get a browser:

* **CDP mode** (default for the desktop app and CLI): attach to the user's own Edge/Chrome that
  :class:`ai_switch.browsers.BrowserManager` started with the app's dedicated profile. Each session
  opens its own tab and closes only that tab afterwards.
* **launch mode** (fallback / tests): Playwright launches its own persistent context.

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

import re

from .config import ProviderConfig, profile_dir, user_selectors_dir
from .errors import MethodFailed, MethodUnavailable, NotLoggedIn

log = logging.getLogger("ai_switch.browser")


class BrowserSession:
    """A signed-in browser for one provider. Use as a context manager."""

    #: Text that means "a human check is on screen" (Cloudflare, reCAPTCHA, hCaptcha, Arkose).
    CHALLENGE_MARKERS = ("verify you are human", "checking your browser", "are you a robot", "i'm not a robot",
                         "complete the security check", "press and hold", "unusual traffic")
    CHALLENGE_SELECTORS = ('iframe[src*="challenges.cloudflare.com"]', 'iframe[src*="recaptcha"]',
                           'iframe[src*="hcaptcha"]', 'iframe[src*="arkoselabs"]', "#challenge-form")

    def __init__(self, config: ProviderConfig, *, headless: bool = False, strict_network: bool = True,
                 profile: Path | None = None, timeout_s: float = 30.0, manager: Any = None,
                 on_challenge: Any = None) -> None:
        self.config = config
        self.manager = manager  # BrowserManager → CDP mode
        self.on_challenge = on_challenge  # callable(session) that waits for the human; None = fail
        self.healed: dict[str, str] = {}
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
        self._pw = sync_playwright().start()
        if self.manager is not None:
            return self._enter_cdp()
        self.profile.mkdir(parents=True, exist_ok=True)
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

    def _enter_cdp(self) -> "BrowserSession":
        try:
            ws = self.manager.launch_debug(visible=self.manager.visible)
            self._cdp_browser = self._pw.chromium.connect_over_cdp(ws)
        except Exception as exc:
            self._pw.stop()
            if isinstance(exc, (MethodFailed, MethodUnavailable)):
                raise
            raise MethodUnavailable(f"could not connect to the browser: {str(exc).splitlines()[0]}",
                                    hint="Close every AI Switch browser window and try again.") from exc
        self._ctx = self._cdp_browser.contexts[0] if self._cdp_browser.contexts else self._cdp_browser.new_context()
        if self.strict_network:
            self._ctx.route("**/*", self._route)
        self._ctx.set_default_timeout(self.timeout_ms)
        self.page = self._ctx.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.blocked_hosts:
            log.info("strict network blocked %d third-party host(s): %s", len(self.blocked_hosts),
                     ", ".join(sorted(self.blocked_hosts)))
        if self.healed:
            log.info("self-healed selectors on %s: %s", self.config.provider, ", ".join(sorted(self.healed)))
        try:
            if self.manager is not None:
                try:
                    if self.strict_network:
                        self._ctx.unroute("**/*")
                    self.page.close()
                except Exception:
                    pass
                self._cdp_browser.close()  # disconnects only; the user's browser keeps running
            elif self._ctx:
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
        self.check_challenge()

    # ------------------------------------------------------------------ human checks (CAPTCHA, 2FA)
    def challenge_present(self) -> bool:
        try:
            for sel in self.CHALLENGE_SELECTORS:
                if self.page.locator(sel).count():
                    return True
            text = (self.page.inner_text("body", timeout=2000) or "")[:4000].lower()
        except Exception:
            return False
        return any(m in text for m in self.CHALLENGE_MARKERS)

    def check_challenge(self) -> None:
        """If a CAPTCHA/human check is showing, hand over to the user and wait until it is gone."""
        self.page.wait_for_timeout(800)
        if not self.challenge_present():
            return
        if self.on_challenge is None:
            raise MethodFailed(f"{self.config.display_name} is showing a 'verify you are human' check",
                               hint="Open the browser window, complete the check, then try again.")
        self.on_challenge(self)

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
        healed = self.heal(key)
        if healed is not None:
            return healed
        if not required:
            return None
        raise MethodFailed(
            f"could not find '{key}' on {self.config.display_name} (tried {len(alternatives)} selector(s))",
            hint=f"The site layout probably changed. Click 'Update fixes', or edit selectors.{key} in "
                 f"{user_selectors_dir() / (self.config.provider + '.yaml')} (see README → Fixing a broken selector).")

    # ------------------------------------------------------------------ self-healing
    _ROLE_WORDS = {"button": ("button", "submit", "send", "save", "add", "import", "start", "open", "manage", "click",
                              "create", "stop", "entry", "menu"),
                   "textbox": ("input", "textbox", "box", "field", "instructions", "about")}

    def heal_candidates(self, key: str) -> list[tuple[str, str]]:
        """(strategy, value) pairs to try when every configured selector for ``key`` failed.

        From ``heal.<key>`` in selectors.yaml (``role`` + ``names``), else guessed from the key name:
        ``add_to_memory`` → a button named "add to memory", ``chat_input`` → a text box.
        """
        hint = (self.config.data.get("heal") or {}).get(key) or {}
        words = [w for w in key.split("_") if w not in ("button", "submit", "entry", "box", "item", "field")]
        role = hint.get("role")
        if not role:
            parts = set(key.split("_"))
            role = ("textbox" if parts & set(self._ROLE_WORDS["textbox"])
                    else "button" if parts & set(self._ROLE_WORDS["button"]) else "")
        names = [str(n) for n in hint.get("names", [])] or ([" ".join(words)] if words else [])
        out: list[tuple[str, str]] = []
        for name in names:
            if role:
                out.append(("role", f"{role}|{name}"))
            out += [("label", name), ("placeholder", name), ("aria", name)]
            if role == "button":
                out.append(("text", name))
        if role == "textbox":
            out += [("css", "textarea"), ("css", '[contenteditable="true"]'), ("role", "textbox|")]
        return out

    def heal(self, key: str) -> Any:
        if key in ("logged_in", "stop_button", "assistant_message", "user_message", "memory_item", "conversation_link"):
            return None  # absence of these is meaningful; never guess
        for strategy, value in self.heal_candidates(key):
            try:
                if strategy == "role":
                    role, name = value.split("|", 1)
                    loc = (self.page.get_by_role(role, name=re.compile(re.escape(name), re.I)) if name
                           else self.page.get_by_role(role))
                elif strategy == "label":
                    loc = self.page.get_by_label(re.compile(re.escape(value), re.I))
                elif strategy == "placeholder":
                    loc = self.page.get_by_placeholder(re.compile(re.escape(value), re.I))
                elif strategy == "aria":
                    loc = self.page.locator(f'[aria-label*="{value}" i], [title*="{value}" i]')
                elif strategy == "text":
                    loc = self.page.get_by_text(re.compile(re.escape(value), re.I))
                else:
                    loc = self.page.locator(value)
                if loc.count() and loc.first.is_visible():
                    self.healed[key] = f"{strategy}:{value}"
                    log.info("selector '%s' on %s healed via %s", key, self.config.provider, strategy)
                    return loc.first
            except Exception:
                continue
        return None

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
                           hint="Click 'Update fixes', or edit selectors." + key + " in "
                                + str(user_selectors_dir() / (self.config.provider + ".yaml")))

    def body_text(self) -> str:
        try:
            return self.page.inner_text("body", timeout=3000)
        except Exception:
            return ""

    # ------------------------------------------------------------------ login + chat
    def ensure_logged_in(self, timeout_ms: int = 15000) -> None:
        if self.manager is not None:
            from .accounts import SIGNED_OUT, CookieInfo, status_from_cookies

            try:
                state = status_from_cookies(self.config, [CookieInfo.from_cdp(c) for c in self.manager.cookies()])
            except Exception:
                state = None
            if state == SIGNED_OUT:
                raise NotLoggedIn(f"not signed in to {self.config.display_name}",
                                  hint=f"Sign in on the Accounts screen (or run: ai-switch login {self.config.provider})")
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
