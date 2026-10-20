"""Local web GUI: a tiny page on http://127.0.0.1 that drives the same flows as the CLI.

* Binds to 127.0.0.1 only and requires a random per-run token on every API call.
* Shows only step messages, counts and masked identifiers — never memory content.
* Questions and "click Done when finished" pauses from the flow appear in the page.
"""
from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .adapters import Context, available
from .errors import AiSwitchError
from .ui import Console

PAGE = Path(__file__).parent / "web" / "index.html"


class _Lines:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, s: str) -> None:
        for part in s.splitlines():
            if part.strip():
                self.lines.append(part)

    def flush(self) -> None:
        pass


class WebConsole(Console):
    """Console whose prompts are answered from the browser page."""

    def __init__(self) -> None:
        self.buf = _Lines()
        super().__init__(interactive=True, out=self.buf)
        self.pending: dict[str, str] | None = None
        self._answer: str = ""
        self._event = threading.Event()

    def _wait(self, kind: str, text: str, default: str = "") -> str:
        self.pending = {"kind": kind, "text": text, "default": default}
        self._event.clear()
        self._event.wait()
        self.pending = None
        return self._answer

    def answer(self, value: str) -> None:
        self._answer = value
        self._event.set()

    def ask(self, question: str, default: str = "") -> str:
        return self._wait("ask", question, default) or default

    def pause(self, msg: str) -> None:
        self.info(f"  → {msg}")
        self._wait("pause", msg)

    def read_block(self, prompt: str, terminator: str = "END-MEMORY") -> str:
        return self._wait("block", prompt)


class Job:
    def __init__(self) -> None:
        self.console = WebConsole()
        self.done = False
        self.error = ""
        self.out_dir = ""
        self.thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        return {"lines": self.console.buf.lines, "pending": self.console.pending, "done": self.done,
                "error": self.error, "out_dir": self.out_dir}


def _run(job: Job, params: dict[str, Any]) -> None:
    from . import migrate

    ctx = Context(console=job.console, out_dir=Path(params.get("out") or "ai-switch-output"),
                  export_path=Path(params["export_path"]).expanduser() if params.get("export_path") else None,
                  offline=bool(params.get("offline")), strict_network=not params.get("loose_network"))
    try:
        if params["command"] == "export":
            res = migrate.export_only(ctx, params["source"], with_chats=bool(params.get("chats")))
        elif params["command"] == "login":
            from .adapters import get_adapter

            get_adapter(params["source"], Context(console=job.console, strict_network=False)).login()
            res = None
        else:
            res = migrate.move(ctx, params["source"], params["target"], with_chats=bool(params.get("chats")),
                               verify=params.get("verify", True))
        job.out_dir = str(res.out_dir) if res else ""
    except AiSwitchError as exc:
        job.error = exc.render()
    except Exception as exc:  # pragma: no cover - surfaced in the page
        job.error = f"Unexpected {type(exc).__name__}"
    finally:
        job.done = True


def make_handler(token: str, state: dict[str, Any]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # keep the terminal quiet
            pass

        def _send(self, code: int, body: Any, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                        "script-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if not self._authorised():
                self._send(403, {"error": "bad token"})
                return
            if self.path == "/api/providers":
                self._send(200, [{"id": k, "name": v.name} for k, v in available().items()])
            elif self.path == "/api/status":
                job = state.get("job")
                self._send(200, job.status() if job else {"lines": [], "done": True, "pending": None})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorised():
                self._send(403, {"error": "bad token"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                params = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "bad json"})
                return
            if self.path == "/api/start":
                job = state.get("job")
                if job and not job.done:
                    self._send(409, {"error": "a run is already in progress"})
                    return
                job = Job()
                state["job"] = job
                job.thread = threading.Thread(target=_run, args=(job, params), daemon=True)
                job.thread.start()
                self._send(200, {"ok": True})
            elif self.path == "/api/answer":
                job = state.get("job")
                if job:
                    job.console.answer(str(params.get("value", "")))
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

    return Handler


def make_server(port: int = 8765) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(16)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token, {}))
    return server, token


def serve(port: int = 8765, open_browser: bool = True) -> int:
    server, token = make_server(port)
    url = f"http://127.0.0.1:{server.server_address[1]}/#token={token}"
    print(f"ai-switch GUI running at {url}\nPress Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
