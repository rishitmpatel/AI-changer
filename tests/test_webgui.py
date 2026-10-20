import json
import threading
import time
import urllib.request

import pytest

from ai_switch.webgui import WebConsole, make_server


@pytest.fixture
def server():
    srv, token = make_server(0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", token
    srv.shutdown()
    srv.server_close()


def call(url, token=None, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Token": token or "", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_page_and_token(server):
    base, token = server
    status, page = call(base + "/")
    assert status == 200 and b"ai-switch" in page
    assert call(base + "/api/providers")[0] == 403
    status, body = call(base + "/api/providers", token)
    assert status == 200 and len(json.loads(body)) == 9


def test_backup_run_via_api(server, fixtures_dir, tmp_path):
    base, token = server
    status, _ = call(base + "/api/start", token, {"command": "export", "source": "claude", "offline": True,
                                                  "export_path": str(fixtures_dir / "claude"),
                                                  "out": str(tmp_path / "gui-out")})
    assert status == 200
    for _ in range(50):
        st = json.loads(call(base + "/api/status", token)[1])
        if st["done"]:
            break
        time.sleep(0.1)
    assert st["done"] and not st["error"], st
    assert any("9 facts" in line for line in st["lines"])
    assert not any("Northwind" in line for line in st["lines"])


def test_webconsole_prompt_roundtrip():
    c = WebConsole()
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("v", c.ask("Numbers to REMOVE", "all")))
    t.start()
    for _ in range(50):
        if c.pending:
            break
        time.sleep(0.02)
    assert c.pending["kind"] == "ask"
    c.answer("1,2")
    t.join(2)
    assert result["v"] == "1,2"
