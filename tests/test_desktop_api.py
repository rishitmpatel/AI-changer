"""Desktop app backend: HTTP API, the move job and its cards, history, settings, fixes."""
import json
import time
import urllib.error
import urllib.request

import pytest
import yaml

from conftest import FakeAI


def call(base, token, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def wait_job(base, token, until, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = call(base, token, "/api/job")
        if job and until(job):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job never reached the expected state: {job}")


def test_static_ui_and_token(app_server):
    base, token, _ = app_server
    with urllib.request.urlopen(base + "/", timeout=5) as r:
        page = r.read().decode()
        assert "AI Switch" in page and "default-src 'self'" in r.headers["Content-Security-Policy"]
    for asset in ("app.js", "app.css", "icon.svg"):
        with urllib.request.urlopen(f"{base}/{asset}", timeout=5) as r:
            assert r.status == 200
    assert call(base, "wrong", "/api/state")[0] == 403
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(base + "/../pyproject.toml", timeout=5)


def test_state_lists_providers_with_tiles(app_server):
    base, token, _ = app_server
    status, state = call(base, token, "/api/state")
    assert status == 200 and state["version"] == "1.0.0"
    ids = [p["id"] for p in state["providers"]]
    assert len(ids) == 9
    tiles = {p["id"]: p for p in state["providers"]}
    assert tiles["mistral"]["name"] == "Le Chat" and tiles["chatgpt"]["mono"] == "GPT"
    assert {p for p, t in tiles.items() if t["chat_import"]} == {"claude", "gemini"}
    assert tiles["deepseek"]["has_memory"] is False


def test_full_move_with_privacy_card(app_server, world):
    base, token, svc = app_server
    world["chatgpt"] = FakeAI(memory=["Lives in Austin.", "Phone number is +1 415 555 0134.", "Partner is named Sam."])
    status, _ = call(base, token, "/api/move", {"source": "chatgpt", "target": "claude"})
    assert status == 200
    job = wait_job(base, token, lambda j: j["card"] and j["card"]["kind"] == "privacy")
    card = job["card"]
    assert [s["status"] for s in job["steps"]][:2] == ["done", "active"]
    assert card["findings"][0]["kind"] == "phone" and "555" not in card["findings"][0]["masked"]
    assert len(card["facts"]) == 3
    # the user removes the phone number and edits one fact
    facts = [dict(f) for f in card["facts"]]
    facts[2]["text"] = "Partner is named Sam Rivera."
    call(base, token, "/api/job/answer", {"card_id": card["id"], "value": {"remove": [0], "facts": facts}})
    job = wait_job(base, token, lambda j: j["done"])
    assert job["error"] is None, job["error"]
    assert job["result"]["moved"] == job["result"]["total"] == 3
    assert "Partner is named Sam Rivera." in world["claude"].memory
    assert not any("415" in m for m in world["claude"].memory)
    assert all(s["status"] == "done" for s in job["steps"])
    assert any("recalled 3 of 3" in line for line in job["lines"])
    _, hist = call(base, token, "/api/history")
    assert hist[0]["status"] == "ok" and hist[0]["moved"] == 3 and "memory" not in json.dumps(hist).lower()


def test_signin_card_then_auto_continue(app_server, world):
    base, token, svc = app_server
    world["gemini"] = FakeAI(memory=["Lives in Austin."])
    world["mistral"] = FakeAI(logged_in=False)
    call(base, token, "/api/move", {"source": "gemini", "target": "mistral"})
    job = wait_job(base, token, lambda j: j["card"] and j["card"]["kind"] == "signin")
    assert job["card"]["provider"] == "mistral" and job["card"]["auto"] is True
    call(base, token, "/api/accounts/signin", {"provider": "mistral"})  # user clicks "Sign in"
    job = wait_job(base, token, lambda j: j["card"] and j["card"]["kind"] == "privacy")
    call(base, token, "/api/job/answer", {"card_id": job["card"]["id"], "value": {"remove": []}})
    job = wait_job(base, token, lambda j: j["done"])
    assert job["result"]["moved"] == 1 and svc.accounts.signins == ["mistral"]


def test_missing_facts_are_retried_automatically(app_server, world):
    base, token, _ = app_server

    class Forgetful(FakeAI):
        def respond(self, text):
            if "what do you know about me" in text.lower():
                return "You live in Austin."
            return super().respond(text)

    world["claude"] = FakeAI(memory=["Lives in Austin.", "Partner is named Sam."])
    world["chatgpt"] = Forgetful()
    call(base, token, "/api/move", {"source": "claude", "target": "chatgpt"})
    job = wait_job(base, token, lambda j: j["card"] and j["card"]["kind"] == "privacy")
    call(base, token, "/api/job/answer", {"card_id": job["card"]["id"], "value": {"remove": []}})
    job = wait_job(base, token, lambda j: j["done"])
    assert any("sending them again" in line for line in job["lines"])
    assert job["result"]["missing"][0]["text"] == "Partner is named Sam."
    assert world["chatgpt"].sent.count(next(s for s in world["chatgpt"].sent if s.startswith("Please"))) >= 1
    # "Retry missing" button
    assert call(base, token, "/api/job/retry", {})[0] == 200
    job = wait_job(base, token, lambda j: j["done"])
    assert [s["status"] for s in job["steps"]] == ["skipped", "skipped", "done", "done"]
    _, hist = call(base, token, "/api/history")
    assert hist[0]["kind"] == "retry" and hist[1]["status"] == "partial"


def test_failure_is_friendly(app_server, world):
    base, token, _ = app_server
    world["grok"] = FakeAI(broken_selectors={"chat_input", "customize_open"})
    call(base, token, "/api/move", {"source": "grok", "target": "claude"})
    job = wait_job(base, token, lambda j: j["done"] or (j["card"] and j["card"]["kind"] == "manual"))
    if not job["done"]:  # last-resort manual paste card: the user gives up
        call(base, token, "/api/job/cancel", {})
        job = wait_job(base, token, lambda j: j["done"])
    err = job["error"]
    assert err and "Traceback" not in err["title"] + err["message"]
    assert err["details"]  # technical details kept for the collapsible section
    assert job["steps"][0]["status"] == "error"


def test_cancel_during_privacy_review(app_server, world):
    base, token, _ = app_server
    world["claude"] = FakeAI(memory=["Lives in Austin."])
    call(base, token, "/api/move", {"source": "claude", "target": "gemini"})
    wait_job(base, token, lambda j: j["card"] and j["card"]["kind"] == "privacy")
    call(base, token, "/api/job/cancel", {})
    job = wait_job(base, token, lambda j: j["done"])
    assert job["error"]["title"] == "Stopped"
    assert world.get("gemini") is None or not world["gemini"].memory
    _, hist = call(base, token, "/api/history")
    assert hist[0]["status"] == "cancelled"


def test_same_ai_and_double_start_rejected(app_server, world):
    base, token, _ = app_server
    status, body = call(base, token, "/api/move", {"source": "claude", "target": "anthropic"})
    assert status == 400 and "same" in body["error"]["message"]
    world["claude"] = FakeAI(memory=["Lives in Austin."])
    call(base, token, "/api/move", {"source": "claude", "target": "gemini"})
    wait_job(base, token, lambda j: j["card"] is not None)
    status, body = call(base, token, "/api/move", {"source": "claude", "target": "gemini"})
    assert status == 400 and "already running" in body["error"]["message"]
    call(base, token, "/api/job/cancel", {})


def test_accounts_and_reset(app_server, world):
    base, token, _ = app_server
    world["claude"] = FakeAI(logged_in=False)
    _, acc = call(base, token, "/api/accounts")
    assert acc["statuses"]["claude"] == "signed_out" and acc["statuses"]["chatgpt"] == "signed_in"
    call(base, token, "/api/accounts/reset", {})
    _, acc = call(base, token, "/api/accounts")
    assert set(acc["statuses"].values()) == {"signed_out"}


def test_settings_roundtrip_and_validation(app_server, isolated_home):
    base, token, _ = app_server
    status, s = call(base, token, "/api/settings", {"browser": "chrome", "fixes_token": "ghp_secret"})
    assert status == 200 and s["browser"] == "chrome" and s["fixes_token"] == "set"
    assert "ghp_secret" not in json.dumps(call(base, token, "/api/state")[1])
    assert call(base, token, "/api/settings", {"browser": "netscape"})[0] == 400
    assert call(base, token, "/api/settings", {"language": "fr"})[0] == 400
    saved = json.loads((isolated_home / "settings.json").read_text())
    assert saved["browser"] == "chrome"


def test_update_fixes_sanitises(app_services, isolated_home):
    import io
    import threading

    from ai_switch.config import load_config
    from ai_switch.desktop.api import make_server

    good = yaml.safe_dump({"provider": "claude", "selectors": {"send_button": "button#brand-new"},
                           "allowed_domains": ["evil.example"], "urls": {"home": "https://evil.example/x",
                                                                         "new_chat": "https://claude.ai/new"},
                           "exfiltrate": True})

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=0):
        if "claude.yaml" in req.full_url:
            assert req.get_header("Authorization") == "token tok"
            return Resp(good.encode())
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    app_services.fixes_opener = opener
    app_services.settings.fixes_token = "tok"
    server, token = make_server(app_services)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, r = call(base, token, "/api/fixes/update", {})
    finally:
        server.shutdown()
    assert status == 200 and r["updated"] == ["claude"] and "private" in r["failed"]["chatgpt"]
    cfg = load_config("claude")
    assert cfg.selector("send_button") == ["button#brand-new"]
    assert not cfg.host_allowed("evil.example")
    assert cfg.url("home") == "https://claude.ai/new" and "exfiltrate" not in cfg.data
