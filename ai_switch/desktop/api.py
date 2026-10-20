"""Local HTTP API + static UI for the desktop app (127.0.0.1 only, per-run token).

The native window (pywebview) just shows ``http://127.0.0.1:<port>/#token=…``; keeping the
backend plain HTTP lets the same UI be tested in a normal browser.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..accounts import AccountService
from ..adapters import Method, available, get_adapter
from ..browsers import BrowserManager, download_chromium, pick_browser
from ..config import load_config
from ..errors import AiSwitchError
from ..fixes import update_fixes
from .friendly import friendly
from .jobs import MoveJob
from .state import History, Settings

log = logging.getLogger("ai_switch.desktop")
WEB = Path(__file__).resolve().parent.parent / "web" / "app"

# Simple coloured monogram tiles (no company logos).
TILES = {
    "chatgpt": ("GPT", "#0e7c66"), "claude": ("Cl", "#b4532a"), "gemini": ("Ge", "#2f62d8"),
    "grok": ("Gk", "#3a3a40"), "copilot": ("Co", "#0f7ab8"), "perplexity": ("Px", "#1f8a8a"),
    "meta_ai": ("Me", "#5b4bd6"), "deepseek": ("Ds", "#4157c8"), "mistral": ("Lc", "#d9771e"),
}
SHORT_NAMES = {"mistral": "Le Chat", "copilot": "Copilot"}


class Services:
    """Everything the API needs; tests swap pieces for fakes."""

    def __init__(self, *, settings: Settings | None = None, history: History | None = None,
                 manager: BrowserManager | None = None, accounts: AccountService | None = None,
                 browser_factory: Callable | None = None, clipboard: Any = None, offline: bool = False,
                 window: Any = None, fixes_opener: Callable | None = None) -> None:
        self.settings = settings or Settings.load()
        self.history = history or History()
        self.providers = available()
        self.configs = {pid: load_config(pid, cls.DEFAULT_CONFIG) for pid, cls in self.providers.items()}
        self.manager = manager if manager is not None else BrowserManager(choice=self.settings.browser)
        self.accounts = accounts if accounts is not None else AccountService(self.manager, self.configs)
        self.browser_factory = browser_factory
        self.clipboard = clipboard
        self.offline = offline
        self.window = window
        self.fixes_opener = fixes_opener
        self.job: MoveJob | None = None
        self.last_move: MoveJob | None = None
        self.signin_threads: dict[str, threading.Thread] = {}
        self.signin_done: dict[str, threading.Event] = {}
        self.download: dict[str, Any] = {"state": "idle", "done": 0, "total": 0}
        self.ui_seen = False

    def reload_configs(self) -> None:
        for pid, cls in self.providers.items():
            self.configs[pid] = load_config(pid, cls.DEFAULT_CONFIG)

    def provider_list(self) -> list[dict[str, Any]]:
        out = []
        for pid, cls in self.providers.items():
            ad = cls()
            mono, color = TILES.get(pid, (cls.name[:2], "#555"))
            chat_auto = Method.OFFICIAL_FLOW in ad.import_chats_methods and bool(
                (ad.config.data.get("flows") or {}).get("import_chats"))
            out.append({"id": pid, "name": SHORT_NAMES.get(pid, cls.name), "full_name": cls.name, "mono": mono,
                        "color": color, "chat_import": chat_auto, "has_memory": ad.has_memory,
                        "memory_import": next((m.value for m in ad.import_memory_methods), "clipboard")})
        return out

    def browser_info(self) -> dict[str, Any] | None:
        b = pick_browser(self.settings.browser)
        return {"name": b.name, "kind": b.kind} if b else None


def _json(handler: BaseHTTPRequestHandler, code: int, body: Any) -> None:
    data = json.dumps(body).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def make_handler(services: Services, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        # -------------------------------------------------------------- plumbing
        def _authorised(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            file = (WEB / name).resolve()
            if WEB not in file.parents or not file.is_file():
                self.send_error(404)
                return
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if file.suffix == ".js":
                ctype = "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
                                                        "style-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(file.read_bytes())

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if not path.startswith("/api/"):
                self._static(path)
                return
            if not self._authorised():
                _json(self, 403, {"error": "bad token"})
                return
            try:
                _json(self, 200, self.route_get(path))
            except KeyError:
                _json(self, 404, {"error": "not found"})
            except Exception as exc:  # pragma: no cover - surfaced in the UI
                log.exception("GET %s failed", path)
                _json(self, 500, {"error": friendly(exc)})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if not self._authorised():
                _json(self, 403, {"error": "bad token"})
                return
            try:
                body = self._body()
                _json(self, 200, self.route_post(path, body))
            except KeyError:
                _json(self, 404, {"error": "not found"})
            except (ValueError, AiSwitchError) as exc:
                _json(self, 400, {"error": friendly(exc) if isinstance(exc, AiSwitchError) else
                                  {"title": "Can't do that", "message": str(exc), "details": "", "action": ""}})
            except Exception as exc:  # pragma: no cover
                log.exception("POST %s failed", path)
                _json(self, 500, {"error": friendly(exc)})

        # -------------------------------------------------------------- routes
        def route_get(self, path: str) -> Any:
            s = services
            if path == "/api/state":
                s.ui_seen = True  # the UI's JavaScript is running (used by the smoke test)
                return {"version": __version__, "providers": s.provider_list(), "settings": s.settings.public(),
                        "browser": s.browser_info(), "job": s.job.status() if s.job else None,
                        "download": s.download, "platform": sys.platform}
            if path == "/api/accounts":
                statuses = s.accounts.status_all() if s.accounts else {}
                signing = [p for p, t in s.signin_threads.items() if t.is_alive()]
                return {"statuses": {p: ("signing_in" if p in signing else v) for p, v in statuses.items()},
                        "browser": s.browser_info()}
            if path == "/api/job":
                return s.job.status() if s.job else None
            if path == "/api/history":
                return s.history.all()
            raise KeyError(path)

        def route_post(self, path: str, body: dict[str, Any]) -> Any:
            s = services
            if path == "/api/move":
                if s.job and not s.job.done:
                    raise ValueError("a move is already running")
                s.job = MoveJob(s, body["source"], body["target"], chats=bool(body.get("chats")),
                                export_path=body.get("export_path") or None).start()
                s.last_move = s.job
                return {"ok": True}
            if path == "/api/job/answer":
                ok = bool(s.job and s.job.answer(int(body["card_id"]), body.get("value")))
                return {"ok": ok}
            if path == "/api/job/cancel":
                if s.job:
                    s.job.cancel()
                return {"ok": True}
            if path == "/api/job/retry":
                last = s.last_move
                if not last or not last.report or not last.report.missing or not last.memory:
                    raise ValueError("nothing to retry")
                subset = last.memory.subset({f.id for f in last.report.missing})
                s.job = MoveJob(s, last.source, last.target, only_facts=subset).start()
                return {"ok": True}
            if path == "/api/job/clear":
                if s.job and s.job.done:
                    s.job = None
                return {"ok": True}
            if path == "/api/accounts/signin":
                provider = body["provider"]
                if provider not in s.configs:
                    raise ValueError("unknown provider")
                running = s.signin_threads.get(provider)
                if running and running.is_alive():
                    return {"ok": True}
                t = threading.Thread(target=self._sign_in, args=(provider,), daemon=True)
                s.signin_threads[provider] = t
                t.start()
                return {"ok": True}
            if path == "/api/accounts/signin-done":  # "I'm signed in": close the window and check now
                ev = s.signin_done.get(body.get("provider", ""))
                if ev:
                    ev.set()
                return {"ok": bool(ev)}
            if path == "/api/accounts/reset":
                if s.job and not s.job.done:
                    raise ValueError("wait for the current move to finish")
                s.accounts.reset()
                return {"ok": True}
            if path == "/api/browser/visible":
                s.settings.update({"show_browser": bool(body.get("visible"))})
                if s.manager is not None:
                    s.manager.visible = bool(body.get("visible"))
                    try:
                        s.manager.set_visible(bool(body.get("visible")))
                    except Exception:
                        pass
                return {"ok": True}
            if path == "/api/browser/download":
                if s.download["state"] != "running":
                    threading.Thread(target=self._download, daemon=True).start()
                return {"ok": True}
            if path == "/api/settings":
                changes = {k: v for k, v in body.items() if k != "fixes_token" or v != "set"}
                old_browser = s.settings.browser
                s.settings.update(changes)
                if s.settings.browser != old_browser and s.manager is not None:
                    s.manager.close()
                    s.manager.choice, s.manager._browser = s.settings.browser, None
                return s.settings.public()
            if path == "/api/fixes/update":
                kw = {"source": s.settings.fixes_source, "token": s.settings.fixes_token}
                if s.fixes_opener:
                    kw["opener"] = s.fixes_opener
                report = update_fixes(sorted(s.configs), **kw)
                s.reload_configs()
                return {"summary": report.summary(), "updated": report.updated, "failed": report.failed,
                        "unchanged": report.unchanged}
            if path == "/api/open-folder":
                folder = Path(body.get("path") or s.settings.output_dir)
                folder.mkdir(parents=True, exist_ok=True)
                open_folder(folder)
                return {"ok": True}
            if path == "/api/pick-file":
                return {"path": pick_file(s.window)}
            if path == "/api/pick-folder":
                return {"path": pick_folder(s.window)}
            raise KeyError(path)

        # -------------------------------------------------------------- background helpers
        def _sign_in(self, provider: str) -> None:
            done = threading.Event()
            services.signin_done[provider] = done
            try:
                services.accounts.sign_in(provider, cancel=done)
            except Exception:
                log.exception("sign-in for %s failed", provider)

        def _download(self) -> None:
            d = services.download
            d.update(state="running", done=0, total=0, error="")
            try:
                download_chromium(lambda done, total: d.update(done=done, total=total))
                if services.manager is not None:
                    services.manager._browser = None
                d["state"] = "done"
            except Exception as exc:
                d.update(state="error", error=friendly(exc)["message"])

    return Handler


def open_folder(folder: Path) -> None:  # pragma: no cover - opens a file manager
    if sys.platform.startswith("win"):
        os.startfile(str(folder))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def pick_file(window: Any) -> str:  # pragma: no cover - native dialog
    if window is None:
        return ""
    import webview

    result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                       file_types=("Exports (*.zip;*.json;*.csv)", "All files (*.*)"))
    return result[0] if result else ""


def pick_folder(window: Any) -> str:  # pragma: no cover - native dialog
    if window is None:
        return ""
    import webview

    result = window.create_file_dialog(webview.FOLDER_DIALOG)
    return result[0] if result else ""


def make_server(services: Services, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(18)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(services, token))
    server.daemon_threads = True
    return server, token


def ui_url(server: ThreadingHTTPServer, token: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/#token={token}"


__all__ = ["Services", "make_server", "ui_url", "get_adapter"]
