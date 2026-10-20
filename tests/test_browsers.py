"""Browser discovery, the plain/debug launch modes, sign-in detection and self-healing selectors."""
import functools
import http.server
import json
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from ai_switch.accounts import (SIGNED_IN, SIGNED_OUT, UNKNOWN, AccountService, CookieInfo, read_cookie_db,
                                status_from_cookies)
from ai_switch.browsers import BrowserInfo, BrowserManager, _candidates, chromium_download_url, find_browsers, \
    pick_browser
from ai_switch.config import ProviderConfig, load_config


# --------------------------------------------------------------------------- discovery (no browser needed)


def test_windows_candidates_prefer_edge(tmp_path):
    env = {"ProgramFiles": str(tmp_path / "PF"), "ProgramFiles(x86)": str(tmp_path / "PF86"),
           "LOCALAPPDATA": str(tmp_path / "Local")}
    cands = _candidates("win32", env)
    assert cands[0][0] == "edge" and cands[0][2].endswith(r"Microsoft\Edge\Application\msedge.exe")
    assert any(c[0] == "chrome" and "Local" in c[2] for c in cands)


def test_find_and_pick_with_fake_install(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    chrome = tmp_path / "chrome"
    edge.write_text("")
    chrome.write_text("")
    monkeypatch.setattr("ai_switch.browsers._candidates",
                        lambda platform, env: [("edge", "Microsoft Edge", str(edge)), ("chrome", "Google Chrome", str(chrome)),
                                               ("chrome", "Google Chrome", str(tmp_path / "missing"))])
    found = find_browsers("linux", {})
    assert [b.kind for b in found] == ["edge", "chrome"]
    assert pick_browser("auto", "linux", {}).kind == "edge"
    assert pick_browser("chrome", "linux", {}).kind == "chrome"
    custom = tmp_path / "custom"
    custom.write_text("")
    assert find_browsers("linux", {"AI_SWITCH_BROWSER_PATH": str(custom)})[0].name == "Custom browser"


def test_chromium_manifest_parsing():
    manifest = {"channels": {"Stable": {"downloads": {"chrome": [
        {"platform": "win64", "url": "https://storage.googleapis.com/x/win64/chrome-win64.zip"},
        {"platform": "mac-arm64", "url": "https://storage.googleapis.com/x/mac-arm64/chrome-mac-arm64.zip"}]}}}}
    assert chromium_download_url(manifest, "win64").endswith("chrome-win64.zip")
    with pytest.raises(Exception):
        chromium_download_url(manifest, "linux64")


def test_missing_browser_message():
    mgr = BrowserManager(choice="auto")
    mgr._browser = None
    import ai_switch.browsers as b

    orig = b.pick_browser
    b.pick_browser = lambda *a, **k: None
    try:
        with pytest.raises(Exception) as e:
            _ = mgr.browser
        assert "Edge or Chrome" in str(e.value)
    finally:
        b.pick_browser = orig


# --------------------------------------------------------------------------- sign-in detection (no browser needed)


def cfg(provider):
    return load_config(provider)


def test_status_from_cookie_rules():
    now = time.time()
    claude = cfg("claude")
    assert status_from_cookies(claude, [CookieInfo("sessionKey", ".claude.ai", now + 3600)]) == SIGNED_IN
    assert status_from_cookies(claude, [CookieInfo("sessionKey", ".claude.ai", now - 10)]) == SIGNED_OUT  # expired
    assert status_from_cookies(claude, [CookieInfo("sessionKey", ".evil.example", now + 99)]) == SIGNED_OUT
    gpt = cfg("chatgpt")  # prefix rule: chunked next-auth cookies
    assert status_from_cookies(gpt, [CookieInfo("__Secure-next-auth.session-token.0", "chatgpt.com", -1)]) == SIGNED_IN
    assert status_from_cookies(cfg("gemini"), [CookieInfo("SID", ".google.com", now + 99)]) == SIGNED_IN
    assert status_from_cookies(cfg("deepseek"), []) == UNKNOWN  # no rule → page check later


def test_cookie_db_reader(tmp_path):
    db_dir = tmp_path / "Default" / "Network"
    db_dir.mkdir(parents=True)
    con = sqlite3.connect(db_dir / "Cookies")
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER)")
    future = int((time.time() + 86400 + 11644473600) * 1_000_000)
    con.execute("INSERT INTO cookies VALUES ('.claude.ai', 'sessionKey', '', x'00', ?)", (future,))
    con.commit()
    con.close()
    cookies = read_cookie_db(tmp_path)
    assert cookies[0].name == "sessionKey" and cookies[0].expires > time.time()
    assert status_from_cookies(cfg("claude"), cookies) == SIGNED_IN
    assert read_cookie_db(tmp_path / "empty-profile") == []


# --------------------------------------------------------------------------- real browser


@pytest.fixture
def chromium(chromium_path):
    if not chromium_path:
        pytest.skip("no Chromium available")
    return BrowserInfo("chromium", "Chromium", chromium_path)


@pytest.fixture(scope="module")
def probe_server():
    """Serves a page that reports navigator.webdriver back to us (no automation attached)."""
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/r?"):
                got.update(urllib.parse.parse_qs(self.path[3:]))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>probe</title><script>fetch('/r?wd='+navigator.webdriver)</script>")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/", got
    srv.shutdown()


@pytest.mark.browser
def test_plain_launch_looks_like_a_normal_browser(chromium, tmp_path, probe_server):
    """Sign-in mode must not expose automation (Google blocks those sign-ins)."""
    url, got = probe_server
    mgr = BrowserManager(chromium, profile=tmp_path / "p", extra_args=("--headless=new",))
    try:
        got.clear()
        mgr.launch_plain(url)
        for _ in range(150):
            if got:
                break
            time.sleep(0.1)
        assert got.get("wd") == ["false"], got
        assert mgr.endpoint() is None  # no debugging port at all
    finally:
        mgr.close()
    assert not mgr.is_running()


@pytest.mark.browser
def test_debug_launch_cdp_cookies_and_session(chromium, tmp_path):
    from ai_switch.browser import BrowserSession

    mgr = BrowserManager(chromium, profile=tmp_path / "p", extra_args=("--headless=new",))
    try:
        ws = mgr.launch_debug()
        assert ws.startswith("ws://127.0.0.1:")
        assert mgr.launch_debug() == ws  # reused, not relaunched
        cdp = mgr.cdp()
        cdp.call("Storage.setCookies", {"cookies": [{"name": "sessionKey", "value": "x", "domain": ".claude.ai",
                                                     "path": "/", "expires": time.time() + 3600}]})
        cdp.close()
        accounts = AccountService(mgr, {"claude": cfg("claude"), "chatgpt": cfg("chatgpt")})
        assert accounts.status_all() == {"claude": SIGNED_IN, "chatgpt": SIGNED_OUT}
        # a BrowserSession attaches over CDP, opens its own tab, and closes only that tab
        config = ProviderConfig("fake", {"urls": {"home": "data:text/html,<p id=me>hi</p>"},
                                         "selectors": {"logged_in": "#me"}})
        with BrowserSession(config, manager=mgr, strict_network=False) as s:
            s.goto("home")
            assert s.find("logged_in") is not None
        assert mgr.is_running()
    finally:
        mgr.close()


@pytest.mark.browser
def test_self_healing_and_challenge(chromium, tmp_path):
    from ai_switch.browser import BrowserSession

    html = ("<button aria-label='Add to memory'>Save it</button>"
            "<textarea placeholder='Paste your memories here'></textarea>")
    config = ProviderConfig("fake", {"urls": {"home": "data:text/html," + html,
                                              "captcha": "data:text/html,<h1>Verify you are human</h1>"},
                                     "selectors": {"add_to_memory": "#old-id-that-no-longer-exists",
                                                   "import_textbox": ".old-class"},
                                     "heal": {"import_textbox": {"role": "textbox", "names": ["paste your memories"]}}})
    mgr = BrowserManager(chromium, profile=tmp_path / "p", extra_args=("--headless=new",))
    seen = []
    try:
        with BrowserSession(config, manager=mgr, strict_network=False, timeout_s=1,
                            on_challenge=lambda sess: seen.append(sess.challenge_present())) as s:
            s.goto("home")
            s.click("add_to_memory")
            s.fill("import_textbox", "hello")
            assert set(s.healed) == {"add_to_memory", "import_textbox"}
            assert s.page.input_value("textarea") == "hello"
            s.goto("captcha")
            assert seen == [True]  # human check handed to the app
    finally:
        mgr.close()


@pytest.mark.browser
def test_sign_in_flow_detects_login_automatically(chromium, tmp_path):
    """Plain browser opens the login page; the site sets its session cookie; AI Switch notices it in
    the profile's cookie database by itself (Chromium flushes cookies to disk within ~30 s), closes
    the browser and reports 'signed in' — no button press needed."""

    class Site(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if self.path.startswith("/login"):
                self.send_header("Set-Cookie", "ais_session=ok; Max-Age=86400; Path=/")
            self.end_headers()
            self.wfile.write(b"<title>signed in</title>")

    import sys

    if sys.platform.startswith("win"):
        pytest.skip("Windows locks the cookie database while the browser runs; see test_sign_in_finishes_when_window_closes")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    config = ProviderConfig("fakeai", {"urls": {"home": base + "/", "login": base + "/login"},
                                       "login": {"cookies": ["ais_session"], "domains": ["127.0.0.1"]}})
    mgr = BrowserManager(chromium, profile=tmp_path / "p", extra_args=("--headless=new",))
    accounts = AccountService(mgr, {"fakeai": config})
    updates = []
    try:
        result = accounts.sign_in("fakeai", on_update=updates.append, poll=0.5, timeout=90)
    finally:
        mgr.close()
        srv.shutdown()
    assert result == SIGNED_IN, updates
    assert updates[0] == "waiting" and "detected" in updates and updates[-1] == SIGNED_IN
    assert not mgr.is_running()  # browser closed by itself after the sign-in
    assert accounts.status("fakeai") == SIGNED_IN  # persisted in the profile for next time


class _FakeManager:
    """Models Windows: cookie DB unreadable while the browser runs; the user then closes the window."""

    def __init__(self, profile, closes_after):
        self.profile, self.closes_after, self.mode, self.started = profile, closes_after, None, None
        self.closed_by_app = False

    def launch_plain(self, url):
        self.mode, self.started = "plain", time.monotonic()

    def is_running(self):
        return self.mode is not None and time.monotonic() - self.started < self.closes_after

    def close(self):
        self.closed_by_app = self.is_running()
        self.mode = None
        db = self.profile / "Default" / "Network"
        db.mkdir(parents=True, exist_ok=True)  # the browser saved its cookies while closing
        con = sqlite3.connect(db / "Cookies")
        con.execute("CREATE TABLE IF NOT EXISTS cookies (host_key TEXT, name TEXT, expires_utc INTEGER)")
        con.execute("INSERT INTO cookies VALUES ('.claude.ai', 'sessionKey', 0)")
        con.commit()
        con.close()

    def endpoint(self):
        return None


@pytest.mark.parametrize("how", ["user closes window", "user clicks I'm signed in"])
def test_sign_in_finishes_when_window_closes(tmp_path, monkeypatch, how):
    import ai_switch.accounts as acc

    mgr = _FakeManager(tmp_path, closes_after=1.0 if how == "user closes window" else 999)
    real_reader = acc.read_cookie_db
    monkeypatch.setattr(acc, "read_cookie_db", lambda p: None if mgr.is_running() else real_reader(p))
    service = AccountService(mgr, {"claude": cfg("claude")})
    done = threading.Event()
    if how != "user closes window":
        threading.Timer(0.5, done.set).start()
    assert service.sign_in("claude", cancel=done, poll=0.1, timeout=10) == SIGNED_IN
    assert mgr.closed_by_app == (how != "user closes window")
