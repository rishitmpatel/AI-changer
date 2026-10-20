"""Packaging: the PyInstaller spec, icons, CI workflows and the app's built-in self-test.

The real Windows/macOS executables are built and launched by .github/workflows/build.yml.
"""
import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

import ai_switch
from ai_switch.desktop.main import main, self_test

ROOT = Path(__file__).parents[1]


def test_version_is_consistent():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == ai_switch.__version__ == "1.0.0"
    assert "ai-switch-app" in pyproject["project"]["gui-scripts"]


def test_spec_references_real_files():
    spec = (ROOT / "packaging" / "ai-switch.spec").read_text(encoding="utf-8")
    for rel in ("packaging/launcher.py", "packaging/icon.ico", "packaging/icon.icns"):
        assert (ROOT / rel).is_file(), rel
    assert 'name="AI Switch"' in spec and 'name="AI Switch.app"' in spec
    assert "console=False" in spec and "upx=False" in spec  # windowed, no UPX (antivirus)
    assert "exclude_binaries=True" in spec and "COLLECT(" in spec  # --onedir, not --onefile
    assert 'collect_data_files("ai_switch")' in spec and 'collect_data_files("playwright")' in spec


def test_icons():
    PIL = pytest.importorskip("PIL.Image")
    ico = PIL.open(ROOT / "packaging" / "icon.ico")
    assert (256, 256) in ico.info["sizes"] and (16, 16) in ico.info["sizes"]
    assert PIL.open(ROOT / "packaging" / "icon.icns").size == (1024, 1024)
    assert (ROOT / "ai_switch" / "web" / "app" / "icon.svg").read_text(encoding="utf-8").startswith("<svg")


def test_build_workflow_smoke_tests_and_release():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))
    on = wf[True] if True in wf else wf["on"]  # YAML 1.1 parses "on" as True
    assert on["push"]["tags"] == ["v*"]
    assert "release_tag" in on["workflow_dispatch"]["inputs"]  # release without pushing a tag
    win = "\n".join(str(s.get("run", "")) for s in wf["jobs"]["windows"]["steps"])
    assert "pyinstaller packaging/ai-switch.spec" in win
    assert "--self-test" in win and "--with-browser" in win and "--smoke-seconds" in win
    assert "AI-Switch-Windows.zip" in win
    mac = "\n".join(str(s.get("run", "")) for s in wf["jobs"]["macos"]["steps"])
    assert "AI-Switch-macOS.zip" in mac and "--self-test" in mac
    rel = wf["jobs"]["release"]
    assert set(rel["needs"]) == {"windows", "macos"}
    run = rel["steps"][-1]["run"]
    assert "gh release create" in run and "AI-Switch-Windows.zip" in run and "AI-Switch-macOS.zip" in run


def test_release_notes_explain_smartscreen():
    notes = (ROOT / "packaging" / "release-notes.md").read_text(encoding="utf-8")
    assert "More info → Run anyway" in notes and "AI Switch.exe" in notes


@pytest.mark.browser
def test_self_test_in_process(tmp_path, chromium_path, monkeypatch):
    pytest.importorskip("webview")
    if not chromium_path:
        pytest.skip("no Chromium available")
    monkeypatch.setenv("AI_SWITCH_BROWSER_PATH", chromium_path)
    report = tmp_path / "selftest.json"
    assert self_test(report, with_browser=True) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] and data["adapter_count"]["value"] == 9 and data["server"]["value"] == 9
    assert "CDP ok" in data["browser_cdp"]["value"]


def test_cli_entry_self_test_without_browser(tmp_path):
    pytest.importorskip("webview")
    report = tmp_path / "r.json"
    assert main(["--self-test", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["version"] == ai_switch.__version__


def test_no_network_libraries_besides_browser_and_fixes():
    """Memory never leaves the computer except via the browser to the two AI sites."""
    offenders = []
    for py in (ROOT / "ai_switch").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if re.search(r"^\s*(import|from)\s+(requests|httpx|aiohttp|urllib3|socket)\b", text, re.M):
            offenders.append(py.name)
        if "urlopen" in text and py.name not in ("fixes.py", "browsers.py", "main.py"):
            offenders.append(py.name)
    assert offenders == []
