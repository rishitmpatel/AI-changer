"""Entry point of the AI Switch desktop app (what ``AI Switch.exe`` runs).

    AI Switch.exe                      open the app window
    AI Switch.exe --self-test FILE     check the bundle works (CI smoke test), write a JSON report
    AI Switch.exe --no-window          only start the local server and print its URL

Window: pywebview (WebView2 on Windows, WKWebView on macOS). If that isn't available the
UI opens in Edge/Chrome "app mode" (a chrome-less window) with a separate UI profile.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import home_dir

log = logging.getLogger("ai_switch.desktop")
APP_NAME = "AI Switch"


def setup_logging() -> Path:
    logs = home_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "ai-switch.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("ai_switch")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path


def _out(msg: str) -> None:
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except Exception:
            pass


# --------------------------------------------------------------------------- self-test


def self_test(report_path: Path, with_browser: bool = False) -> int:
    checks: dict[str, Any] = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
                              "platform": sys.platform}
    ok = True

    def check(name: str, fn, required: bool = True) -> None:
        nonlocal ok
        try:
            checks[name] = {"ok": True, "value": fn()}
        except Exception as exc:  # report, keep going
            checks[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            ok = ok and not required

    from ..adapters import available
    from ..config import load_config

    check("adapters", lambda: sorted(available()))
    check("adapter_count", lambda: _require(len(available()) == 9, len(available())))
    check("selectors", lambda: {p: bool(load_config(p).selector("chat_input")) for p in available()})
    web = Path(__file__).resolve().parent.parent / "web" / "app"
    check("web_assets", lambda: _require(all((web / f).is_file() for f in ("index.html", "app.js", "app.css", "icon.svg")),
                                         str(web)))
    check("server", _server_roundtrip)
    check("playwright_driver", _playwright_driver)
    check("websocket", lambda: __import__("websocket").__name__)
    check("webview", lambda: __import__("webview").__name__)
    from ..browsers import find_browsers

    check("browsers", lambda: [b.name for b in find_browsers()], required=False)
    if with_browser:
        check("browser_signin_mode", _plain_probe)
        check("browser_cdp", _browser_roundtrip)
    checks["ok"] = ok
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(checks, indent=2), encoding="utf-8")
    _out(json.dumps(checks, indent=2))
    return 0 if ok else 1


def _require(cond: bool, value: Any) -> Any:
    if not cond:
        raise AssertionError(f"unexpected value: {value!r}")
    return value


def _server_roundtrip() -> int:
    from ..browsers import BrowserManager
    from .api import Services, make_server

    svc = Services(manager=BrowserManager(), accounts=None)
    server, token = make_server(svc)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
        _require(b"AI Switch" in page, len(page))
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Token": token})
        try:
            state = json.load(urllib.request.urlopen(req, timeout=10))
        except urllib.error.HTTPError as exc:  # show the server's own error in the report
            body = exc.read().decode("utf-8", "replace")
            raise AssertionError(f"/api/state returned {exc.code}: {body[-1500:]}") from None
        return _require(len(state["providers"]) == 9, len(state["providers"]))
    finally:
        server.shutdown()
        server.server_close()


def _playwright_driver() -> str:
    from playwright._impl._driver import compute_driver_executable

    exe = compute_driver_executable()
    paths = exe if isinstance(exe, tuple) else (exe,)
    _require(all(Path(p).exists() for p in paths), paths)
    return str(paths[0])


def _plain_probe() -> str:
    """Sign-in mode: the browser starts normally and a page sees navigator.webdriver === false."""
    import http.server
    import tempfile
    import urllib.parse

    from ..browsers import BrowserManager

    got: dict[str, Any] = {}

    class Probe(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/r?"):
                got.update(urllib.parse.parse_qs(self.path[3:]))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<script>fetch('/r?wd='+navigator.webdriver)</script>")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Probe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mgr = BrowserManager(profile=Path(tempfile.mkdtemp(prefix="ais-probe-")), extra_args=("--headless=new",))
    try:
        mgr.launch_plain(f"http://127.0.0.1:{srv.server_address[1]}/")
        deadline = time.monotonic() + 30
        while not got and time.monotonic() < deadline:
            time.sleep(0.2)
        _require(got.get("wd") == ["false"], got or "page never loaded")
        _require(mgr.endpoint() is None, "debug port open in sign-in mode")
        return f"{mgr.browser.name}: navigator.webdriver = false, no debugging port"
    finally:
        mgr.close()
        srv.shutdown()


def _browser_roundtrip() -> str:
    """Launch the system browser the way the app does, attach over CDP, run one script."""
    import tempfile

    from playwright.sync_api import sync_playwright

    from ..browsers import BrowserManager

    mgr = BrowserManager(profile=Path(tempfile.mkdtemp(prefix="ais-selftest-")), extra_args=("--headless=new",))
    try:
        ws = mgr.launch_debug()
        cookies = mgr.cookies()
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(ws)
            page = b.contexts[0].new_page()
            page.goto("data:text/html,<title>ok</title>")
            value = page.evaluate("1 + 1")
            b.close()
        _require(value == 2, value)
        return f"{mgr.browser.name}: CDP ok, {len(cookies)} cookies"
    finally:
        mgr.close()


# --------------------------------------------------------------------------- window


def open_window(url: str, services: Any, smoke: dict[str, Any] | None = None) -> str:
    """Show the UI; blocks until the window is closed. Returns which backend was used."""
    title = f"{APP_NAME} {__version__}"
    try:
        import webview

        win = webview.create_window(title, url, width=1080, height=700, min_size=(880, 560),
                                    background_color="#f4f5f7", text_select=True)
        services.window = win
        if smoke is not None:
            win.events.loaded += lambda: smoke.update(loaded=True)
            threading.Thread(target=_smoke_close, args=(smoke, win.destroy), daemon=True).start()
        storage = home_dir() / "webview"
        webview.start(private_mode=True, storage_path=str(storage))
        return "pywebview"
    except Exception as exc:
        log.warning("pywebview unavailable (%s); opening in app mode", type(exc).__name__)
    from ..browsers import pick_browser

    b = pick_browser(services.settings.browser)
    if b is not None:
        args = [b.path, f"--app={url}", f"--user-data-dir={home_dir() / 'ui-profile'}", "--window-size=1080,700",
                "--no-first-run", "--no-default-browser-check"]
        from ..browsers import no_sandbox_needed

        if no_sandbox_needed():
            args.append("--no-sandbox")
        proc = subprocess.Popen(args)
        if smoke is not None:
            smoke["loaded"] = True
            threading.Thread(target=_smoke_close, args=(smoke, proc.terminate), daemon=True).start()
        proc.wait()
        return "app-mode"
    import webbrowser

    webbrowser.open(url)
    _out(f"AI Switch is running at {url} — close this window to quit.")
    if smoke is not None:
        smoke["loaded"] = True
        _smoke_close(smoke, lambda: None)
        return "system-browser"
    while True:  # pragma: no cover - keeps the server alive for the browser tab
        time.sleep(3600)


def _smoke_close(smoke: dict[str, Any], close) -> None:
    time.sleep(float(smoke.get("seconds", 8)))
    try:
        close()
    except Exception:
        pass


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Move your AI memory between assistants.")
    parser.add_argument("--self-test", metavar="REPORT", type=Path, help="check the bundle and write a JSON report")
    parser.add_argument("--with-browser", action="store_true", help="self-test also launches the system browser")
    parser.add_argument("--no-window", action="store_true", help="only run the local server")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--smoke-seconds", type=float, default=float(os.environ.get("AI_SWITCH_SMOKE_SECONDS") or 0),
                        help="open the window, then close it after N seconds and write smoke.json (CI)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)
    log_path = setup_logging()
    if args.self_test:
        return self_test(args.self_test, with_browser=args.with_browser)

    from .api import Services, make_server, ui_url

    services = Services()
    server, token = make_server(services, args.port)
    threading.Thread(target=server.serve_forever, daemon=True, name="ai-switch-ui").start()
    url = ui_url(server, token)
    log.info("AI Switch %s started; log at %s", __version__, log_path)
    if args.no_window:
        _out(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    smoke = {"seconds": args.smoke_seconds, "loaded": False} if args.smoke_seconds else None
    try:
        backend = open_window(url, services, smoke)
    finally:
        if services.job and not services.job.done:
            services.job.cancel()
        if services.manager is not None:
            services.manager.close()
        server.shutdown()
    if smoke is not None:
        # "loaded" = the page's JavaScript ran and talked to the backend with its token
        result = {"window": backend, "loaded": bool(services.ui_seen), "window_event": smoke.get("loaded", False),
                  "version": __version__}
        (home_dir() / "smoke.json").write_text(json.dumps(result), encoding="utf-8")
        _out(json.dumps(result))
        return 0 if result["loaded"] else 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
