"""Find and drive the browser the user already has (Edge or Chrome), with its own profile.

Two launch modes, both with the dedicated profile ``<app data>/browser-profile``:

* **plain**  – exactly like double-clicking Edge/Chrome: no automation or debugging switches.
  Used while you sign in, so sites (Google especially) see a normal browser
  (``navigator.webdriver`` is ``false``; Chrome sets it ``true`` as soon as
  ``--remote-debugging-port`` is passed).
* **debug**  – the same profile relaunched with ``--remote-debugging-port=0``; ai-switch then
  connects over CDP. Used only for the automated export/import after you are signed in.

Chrome 136+ ignores the debugging switches on the *default* profile, which is why a
dedicated ``--user-data-dir`` is used (it also keeps your everyday browser untouched).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import home_dir, profile_dir
from .errors import MethodFailed, MethodUnavailable

log = logging.getLogger("ai_switch.browsers")

CFT_JSON = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"


@dataclass(frozen=True)
class BrowserInfo:
    kind: str  # edge | chrome | chromium
    name: str
    path: str


def _candidates(platform: str, env: dict[str, str]) -> list[tuple[str, str, str]]:
    """(kind, display name, path) in preference order for each OS."""
    if platform.startswith("win"):
        pf = env.get("ProgramFiles", r"C:\Program Files")
        pf86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = env.get("LOCALAPPDATA", "")
        out = []
        for base in (pf86, pf, local):
            if base:
                out.append(("edge", "Microsoft Edge", rf"{base}\Microsoft\Edge\Application\msedge.exe"))
        for base in (pf, pf86, local):
            if base:
                out.append(("chrome", "Google Chrome", rf"{base}\Google\Chrome\Application\chrome.exe"))
        return out
    if platform == "darwin":
        out = []
        for root in ("/Applications", str(Path.home() / "Applications")):
            out.append(("chrome", "Google Chrome", f"{root}/Google Chrome.app/Contents/MacOS/Google Chrome"))
            out.append(("edge", "Microsoft Edge", f"{root}/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"))
        return out
    out = []
    for kind, name, exe in (("chrome", "Google Chrome", "google-chrome"), ("chrome", "Google Chrome", "google-chrome-stable"),
                            ("edge", "Microsoft Edge", "microsoft-edge"), ("chromium", "Chromium", "chromium"),
                            ("chromium", "Chromium", "chromium-browser")):
        found = shutil.which(exe)
        if found:
            out.append((kind, name, found))
    return out


def _windows_registry_paths() -> list[tuple[str, str, str]]:  # pragma: no cover - Windows only
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for exe, kind, name in (("msedge.exe", "edge", "Microsoft Edge"), ("chrome.exe", "chrome", "Google Chrome")):
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                    out.append((kind, name, winreg.QueryValue(key, None)))
            except OSError:
                continue
    return out


def downloaded_chromium_dir() -> Path:
    return home_dir() / "chromium"


def _downloaded_chromium() -> list[tuple[str, str, str]]:
    root = downloaded_chromium_dir()
    for rel in ("chrome-win64/chrome.exe", "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", "chrome-linux64/chrome"):
        if (root / rel).exists():
            return [("chromium", "Chromium (downloaded by AI Switch)", str(root / rel))]
    return []


def find_browsers(platform: str | None = None, env: dict[str, str] | None = None) -> list[BrowserInfo]:
    """Every usable browser, preferred first. ``AI_SWITCH_BROWSER_PATH`` overrides everything."""
    platform = platform or sys.platform
    env = dict(os.environ if env is None else env)
    found: list[BrowserInfo] = []
    seen: set[str] = set()
    raw = []
    if env.get("AI_SWITCH_BROWSER_PATH"):
        raw.append(("chromium", "Custom browser", env["AI_SWITCH_BROWSER_PATH"]))
    raw += _candidates(platform, env)
    if platform.startswith("win"):
        raw += _windows_registry_paths()
    raw += _downloaded_chromium()
    for kind, name, path in raw:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen or not Path(path).is_file():
            continue
        seen.add(key)
        found.append(BrowserInfo(kind, name, path))
    return found


def pick_browser(choice: str = "auto", platform: str | None = None, env: dict[str, str] | None = None) -> BrowserInfo | None:
    browsers = find_browsers(platform, env)
    if choice in ("edge", "chrome"):
        for b in browsers:
            if b.kind == choice:
                return b
    return browsers[0] if browsers else None


# --------------------------------------------------------------------------- Chromium download (last resort)


def cft_platform(platform: str | None = None, machine: str | None = None) -> str:
    import platform as _p

    platform = platform or sys.platform
    machine = (machine or _p.machine()).lower()
    if platform.startswith("win"):
        return "win64"
    if platform == "darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    return "linux64"


def chromium_download_url(manifest: dict[str, Any], plat: str) -> str:
    for item in manifest["channels"]["Stable"]["downloads"]["chrome"]:
        if item["platform"] == plat:
            return item["url"]
    raise MethodUnavailable(f"no Chromium build for {plat}")


def download_chromium(progress: Callable[[int, int], None] | None = None, *, opener=urllib.request.urlopen) -> BrowserInfo:
    """Download Chrome for Testing (Google's official Chromium build) into the app folder.

    Only used when neither Edge nor Chrome is installed. Contacts googlechromelabs.github.io and
    storage.googleapis.com (Google's own download servers) and nothing else.
    """
    with opener(CFT_JSON, timeout=30) as r:
        manifest = json.load(r)
    url = chromium_download_url(manifest, cft_platform())
    dest = downloaded_chromium_dir()
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / "download.zip"
    with opener(url, timeout=60) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    with zipfile.ZipFile(tmp) as z:
        z.extractall(dest)
    tmp.unlink()
    for exe in dest.rglob("*"):  # restore exec bits lost by zipfile
        if exe.is_file() and (exe.suffix == "" or exe.name.endswith((".dylib", ".so"))):
            try:
                exe.chmod(exe.stat().st_mode | 0o111)
            except OSError:
                pass
    found = _downloaded_chromium()
    if not found:
        raise MethodFailed("the Chromium download did not contain a browser")
    kind, name, path = found[0]
    return BrowserInfo(kind, name, path)


# --------------------------------------------------------------------------- CDP (browser level only)


class CDP:
    """Minimal browser-level DevTools client. Never attaches to pages unless asked."""

    def __init__(self, ws_url: str, timeout: float = 15) -> None:
        import websocket  # websocket-client

        self.ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None, session: str | None = None) -> dict[str, Any]:
        self._id += 1
        msg: dict[str, Any] = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise MethodFailed(f"browser refused {method}: {reply['error'].get('message')}")
                return reply.get("result", {})

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


class BrowserManager:
    """Owns the app's browser process (one at a time, one dedicated profile)."""

    def __init__(self, browser: BrowserInfo | None = None, *, choice: str = "auto", profile: Path | None = None,
                 extra_args: tuple[str, ...] = ()) -> None:
        self.choice = choice
        self._browser = browser
        self.profile = Path(profile or profile_dir())
        self.extra_args = tuple(extra_args)
        self.proc: subprocess.Popen | None = None
        self.mode: str | None = None  # plain | debug
        self.visible = False

    # ------------------------------------------------------------------ discovery
    @property
    def browser(self) -> BrowserInfo:
        if self._browser is None:
            self._browser = pick_browser(self.choice)
        if self._browser is None:
            raise MethodUnavailable("no Edge or Chrome found on this computer",
                                    hint="Install Microsoft Edge or Google Chrome, or let AI Switch download Chromium.")
        return self._browser

    def _base_args(self) -> list[str]:
        args = [self.browser.path, f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check"]
        if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")  # Chromium refuses to run as root otherwise (containers/CI)
        return args + list(self.extra_args)

    # ------------------------------------------------------------------ state
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _port_file(self) -> Path:
        return self.profile / "DevToolsActivePort"

    def endpoint(self) -> str | None:
        """ws:// URL of the running debug browser, or None."""
        pf = self._port_file()
        if not self.is_running() or self.mode != "debug" or not pf.exists():
            return None
        parts = pf.read_text().split()
        if len(parts) != 2:
            return None
        return f"ws://127.0.0.1:{parts[0]}{parts[1]}"

    def http_endpoint(self) -> str | None:
        pf = self._port_file()
        if self.endpoint() is None:
            return None
        return f"http://127.0.0.1:{pf.read_text().split()[0]}"

    # ------------------------------------------------------------------ launching
    def launch_plain(self, url: str) -> None:
        """Start the browser exactly like a person would (no debugging switches)."""
        self.close()
        self.profile.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(self._base_args() + ["--new-window", url], **_popen_kwargs())
        self.mode = "plain"
        self.visible = True
        log.info("browser started for sign-in (%s, no debugging port)", self.browser.kind)

    def launch_debug(self, *, visible: bool = False, timeout: float = 30) -> str:
        """Start (or reuse) the debug-mode browser and return its CDP websocket URL."""
        if self.mode == "debug" and self.endpoint():
            return self.endpoint()  # type: ignore[return-value]
        self.close()
        self.profile.mkdir(parents=True, exist_ok=True)
        try:
            self._port_file().unlink()
        except FileNotFoundError:
            pass
        self.proc = subprocess.Popen(self._base_args() + ["--remote-debugging-port=0", "about:blank"], **_popen_kwargs())
        self.mode = "debug"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise MethodFailed("the browser closed straight after starting",
                                   hint="Close every window of the AI Switch browser and try again.")
            ws = self.endpoint()
            if ws:
                self.set_visible(visible)
                log.info("browser started for automation (%s)", self.browser.kind)
                return ws
            time.sleep(0.1)
        self.close()
        raise MethodFailed("the browser did not open its automation port in time",
                           hint="Close other AI Switch browser windows, then try again.")

    def wait_closed(self, timeout: float | None = None) -> bool:
        if not self.proc:
            return True
        try:
            self.proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def close(self, timeout: float = 10) -> None:
        """Close gracefully (lets the browser save cookies), then force if needed."""
        if not self.is_running():
            self.proc, self.mode = None, None
            return
        ws = self.endpoint()
        try:
            if ws:
                cdp = CDP(ws)
                try:
                    cdp.call("Browser.close")
                except Exception:
                    pass
                cdp.close()
            elif sys.platform.startswith("win"):  # pragma: no cover - Windows only
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T"], capture_output=True)
            else:
                self.proc.terminate()  # SIGTERM: Chrome shuts down cleanly
            self.proc.wait(timeout=timeout)
        except Exception:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:
                pass
        self.proc, self.mode = None, None

    # ------------------------------------------------------------------ browser-level helpers
    def cdp(self) -> CDP:
        ws = self.endpoint() or self.launch_debug()
        return CDP(ws)

    def cookies(self) -> list[dict[str, Any]]:
        cdp = self.cdp()
        try:
            return cdp.call("Storage.getCookies").get("cookies", [])
        finally:
            cdp.close()

    def set_visible(self, visible: bool) -> None:
        """Show or minimise every browser window (no page is attached for this)."""
        self.visible = visible
        ws = self.endpoint()
        if not ws:
            return
        cdp = CDP(ws)
        try:
            for t in cdp.call("Target.getTargets").get("targetInfos", []):
                if t.get("type") != "page":
                    continue
                try:
                    win = cdp.call("Browser.getWindowForTarget", {"targetId": t["targetId"]})["windowId"]
                    state = "normal" if visible else "minimized"
                    cdp.call("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": state}})
                except MethodFailed:
                    continue  # headless browsers have no windows
        finally:
            cdp.close()


def _popen_kwargs() -> dict[str, Any]:
    kw: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if sys.platform.startswith("win"):  # pragma: no cover
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True
    return kw
