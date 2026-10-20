import json

from ai_switch.cli import main


def test_providers_table(capsys):
    assert main(["providers"]) == 0
    out = capsys.readouterr().out
    for pid in ("chatgpt", "claude", "gemini", "grok", "copilot", "perplexity", "meta_ai", "deepseek", "mistral"):
        assert pid in out


def test_export_backup_offline(tmp_path, fixtures_dir, export_zip, capsys):
    code = main(["export", "--from", "anthropic", "--export", str(export_zip("claude")), "--chats", "--offline",
                 "--yes", "--out", str(tmp_path / "o")])
    assert code == 0
    out_dirs = list((tmp_path / "o").iterdir())
    assert len(out_dirs) == 1
    mem = json.loads((out_dirs[0] / "memory.json").read_text(encoding="utf-8"))
    assert mem["source_provider"] == "claude"
    assert (out_dirs[0] / "chats.json").exists()
    printed = capsys.readouterr().out
    assert "Northwind" not in printed and "9 facts" in printed


def test_move_offline_noninteractive_writes_parts(tmp_path, export_zip, capsys):
    code = main(["move", "--from", "claude", "--to", "chatgpt", "--export", str(export_zip("claude")),
                 "--offline", "--yes", "--no-verify", "--redact", "all", "--out", str(tmp_path / "o")])
    assert code == 0
    out_dir = next((tmp_path / "o").iterdir())
    parts = list((out_dir / "import_parts" / "chatgpt-clipboard").glob("part-*.txt"))
    assert parts and parts[0].read_text(encoding="utf-8").startswith("Please remember")


def test_unknown_provider_error(capsys):
    assert main(["export", "--from", "clippy", "--offline", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "Choosing providers failed" in err and "Known providers" in err


def test_missing_export_file_error(tmp_path, capsys):
    code = main(["export", "--from", "chatgpt", "--export", str(tmp_path / "missing.zip"), "--offline", "--yes",
                 "--out", str(tmp_path / "o")])
    assert code == 1
    err = capsys.readouterr().err
    assert "Export memory from ChatGPT failed" in err
    assert "What you can do:" in err
