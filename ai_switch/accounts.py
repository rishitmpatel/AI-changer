"""Sign-in status per provider, and the sign-in flow that keeps Google happy.

Status is decided from cookies only (never by touching a page):

* while the debug-mode browser runs → ``Storage.getCookies`` over CDP (browser level),
* otherwise → read the profile's cookie database read-only (names and expiry only; values
  are never read), which works while the browser is closed and, on macOS/Linux, while it runs.

Which cookies mean "signed in" lives in each provider's selectors.yaml under ``login:``,
so "Update fixes" can correct it. Providers without a cookie rule fall back to the
``logged_in`` page selector once the automation browser is up.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .browsers import BrowserManager
from .config import ProviderConfig

log = logging.getLogger("ai_switch.accounts")

SIGNED_IN, SIGNED_OUT, UNKNOWN = "signed_in", "signed_out", "unknown"
_CHROME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


@dataclass
class CookieInfo:
    name: str
    domain: str
    expires: float  # unix seconds; <= 0 means session cookie

    @classmethod
    def from_cdp(cls, c: dict[str, Any]) -> "CookieInfo":
        return cls(c.get("name", ""), c.get("domain", ""), float(c.get("expires", -1) or -1))


def rule_for(config: ProviderConfig) -> dict[str, list[str]]:
    login = config.data.get("login") or {}
    return {"cookies": [str(c) for c in login.get("cookies", [])],
            "domains": [str(d).lstrip(".").lower() for d in login.get("domains", [])] or config.allowed_domains}


def status_from_cookies(config: ProviderConfig, cookies: Iterable[CookieInfo], now: float | None = None) -> str:
    rule = rule_for(config)
    if not rule["cookies"]:
        return UNKNOWN
    now = now or time.time()
    for c in cookies:
        domain = c.domain.lstrip(".").lower()
        if not any(domain == d or domain.endswith("." + d) for d in rule["domains"]):
            continue
        if any(c.name == n or (n.endswith("*") and c.name.startswith(n[:-1])) for n in rule["cookies"]):
            if c.expires <= 0 or c.expires > now:
                return SIGNED_IN
    return SIGNED_OUT


def cookie_db_paths(profile: Path) -> list[Path]:
    return [profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies"]


def read_cookie_db(profile: Path) -> list[CookieInfo] | None:
    """Cookie names/domains/expiry from the profile's database, or None if it can't be read now.

    Copies the file first (Chrome may hold it open); on Windows a running browser locks it,
    in which case this returns None and the caller waits for the window to close instead.
    """
    for db in cookie_db_paths(profile):
        if not db.exists():
            continue
        tmpdir = Path(tempfile.mkdtemp(prefix="ais-cookies-"))
        try:
            copy = tmpdir / "Cookies"
            try:
                shutil.copy2(db, copy)
                for extra in ("-wal", "-journal"):
                    if Path(str(db) + extra).exists():
                        shutil.copy2(str(db) + extra, str(copy) + extra)
            except OSError:
                return None
            con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            try:
                rows = con.execute("SELECT name, host_key, expires_utc FROM cookies").fetchall()
            except sqlite3.DatabaseError:
                return None
            finally:
                con.close()
            out = []
            for name, host, expires in rows:
                exp = (expires / 1_000_000 - _CHROME_EPOCH_OFFSET) if expires else -1
                out.append(CookieInfo(name, host, exp))
            return out
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return []


class AccountService:
    """Checks and performs sign-ins for all providers using one BrowserManager."""

    def __init__(self, manager: BrowserManager, configs: dict[str, ProviderConfig]) -> None:
        self.manager = manager
        self.configs = configs
        self.cache: dict[str, str] = {}
        self.lock = threading.Lock()

    def _cookies(self) -> list[CookieInfo] | None:
        if self.manager.mode == "debug" and self.manager.endpoint():
            return [CookieInfo.from_cdp(c) for c in self.manager.cookies()]
        return read_cookie_db(self.manager.profile)  # browser closed, or plain mode

    def status(self, provider: str, cookies: list[CookieInfo] | None = None) -> str:
        cookies = self._cookies() if cookies is None else cookies
        result = UNKNOWN if cookies is None else status_from_cookies(self.configs[provider], cookies)
        self.cache[provider] = result
        return result

    def status_all(self) -> dict[str, str]:
        with self.lock:
            try:
                cookies = self._cookies()
            except Exception as exc:  # browser missing etc.: show unknown, don't crash the screen
                log.info("could not read sign-in state: %s", type(exc).__name__)
                cookies = None
            return {p: self.status(p, cookies) for p in self.configs}

    def sign_in(self, provider: str, *, on_update: Callable[[str], None] | None = None,
                cancel: threading.Event | None = None, timeout: float = 900, poll: float = 2.0) -> str:
        """Open the provider's sign-in page in a *plain* browser and wait until signed in.

        Finishes as soon as the login cookie appears (auto-detected), or when the user closes
        the browser window. Returns the final status.
        """
        cfg = self.configs[provider]
        url = cfg.url("login") or cfg.url("home")
        say = on_update or (lambda s: None)
        with self.lock:
            self.manager.launch_plain(url)
        say("waiting")
        deadline = time.monotonic() + timeout
        seen_signed_in_at: float | None = None
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():
                break
            if not self.manager.is_running():
                break  # user closed the window
            cookies = read_cookie_db(self.manager.profile)
            if cookies is not None and status_from_cookies(cfg, cookies) == SIGNED_IN:
                seen_signed_in_at = seen_signed_in_at or time.monotonic()
                say("detected")
                if time.monotonic() - seen_signed_in_at > 3:  # let the site finish its redirects
                    break
            time.sleep(poll)
        with self.lock:
            self.manager.close()  # graceful: cookies are flushed to disk
            cookies = read_cookie_db(self.manager.profile)
            result = UNKNOWN if cookies is None else status_from_cookies(cfg, cookies)
            if result == UNKNOWN and rule_for(cfg)["cookies"]:
                try:  # database still busy: ask the browser itself
                    self.manager.launch_debug(visible=False)
                    result = status_from_cookies(cfg, [CookieInfo.from_cdp(c) for c in self.manager.cookies()])
                except Exception:
                    pass
        self.cache[provider] = result
        say(result)
        return result

    def reset(self) -> None:
        """Forget every sign-in by deleting the app's browser profile (your own browser is untouched)."""
        with self.lock:
            self.manager.close()
            shutil.rmtree(self.manager.profile, ignore_errors=True)
            self.cache.clear()
