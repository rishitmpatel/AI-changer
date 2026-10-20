"""selectors.yaml files: complete, consistent, and overridable with one line."""
import pytest
import yaml

from ai_switch.adapters import available, get_adapter
from ai_switch.config import PACKAGE_SELECTORS, load_config

PROVIDERS = sorted(available())
REQUIRED_SELECTORS = ("logged_in", "chat_input", "send_button", "assistant_message")
FLOW_SELECTOR_ACTIONS = {"click", "click_optional", "wait_for", "collect", "collect_value"}


def test_nine_providers():
    assert PROVIDERS == ["chatgpt", "claude", "copilot", "deepseek", "gemini", "grok", "meta_ai", "mistral",
                         "perplexity"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_yaml_complete(provider):
    cfg = load_config(provider)
    assert cfg.sources == [PACKAGE_SELECTORS / f"{provider}.yaml"]
    for key in REQUIRED_SELECTORS:
        assert cfg.selector(key), f"{provider}: selectors.{key} missing"
    assert cfg.url("home").startswith("https://")
    assert cfg.host_allowed(cfg.url("home").split("/")[2])
    assert not cfg.host_allowed("google-analytics.com") or provider == "gemini"
    assert not cfg.host_allowed("doubleclick.net")
    for key in ("export_file", "import_memory", "import_chats"):
        assert cfg.manual(key), f"{provider}: manual_steps.{key} missing"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_flows_only_reference_defined_keys(provider):
    cfg = load_config(provider)
    for name, steps in (cfg.data.get("flows") or {}).items():
        for step in steps:
            action, arg = next(iter(step.items())) if isinstance(step, dict) else (step, None)
            if action == "goto":
                assert cfg.url(arg), f"{provider}.flows.{name}: url '{arg}' undefined"
            elif action in FLOW_SELECTOR_ACTIONS:
                assert cfg.selector(arg), f"{provider}.flows.{name}: selector '{arg}' undefined"
            elif action in ("fill", "append", "upload"):
                assert cfg.selector(arg["into"]), f"{provider}.flows.{name}: selector '{arg['into']}' undefined"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_import_styles_known(provider):
    cfg = load_config(provider)
    for opts in (cfg.data.get("import") or {}).values():
        assert opts["style"] in {"block", "lines", "lines_block", "remember", "profile", "primer"}


def test_user_override_changes_one_line(isolated_home):
    (isolated_home / "selectors").mkdir(parents=True)
    (isolated_home / "selectors" / "chatgpt.yaml").write_text(
        yaml.safe_dump({"selectors": {"send_button": "button#new-send"}}))
    cfg = load_config("chatgpt")
    assert cfg.selector("send_button") == ["button#new-send"]
    assert cfg.selector("chat_input")  # everything else still comes from the packaged file
    assert len(cfg.sources) == 2


def test_bad_override_yaml_is_explained(isolated_home):
    from ai_switch.errors import AiSwitchError

    (isolated_home / "selectors").mkdir(parents=True)
    (isolated_home / "selectors" / "claude.yaml").write_text("selectors: [unclosed")
    with pytest.raises(AiSwitchError) as e:
        load_config("claude")
    assert "not valid YAML" in str(e.value) and e.value.hint


def test_every_adapter_describes_itself():
    for pid in PROVIDERS:
        d = get_adapter(pid).describe()
        assert d["export_memory"] and d["import_memory"][-1] == "clipboard"
