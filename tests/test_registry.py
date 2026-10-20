"""Adding a provider = one adapter file (config can live inside it)."""
from ai_switch import adapters
from ai_switch.adapters import BaseAdapter, Method, get_adapter, register, resolve
from ai_switch.schema import Fact, MemoryDoc
from conftest import FakeAI


def test_aliases():
    assert resolve("OpenAI") == "chatgpt"
    assert resolve("le-chat") == "mistral"
    assert resolve("Meta AI") == "meta_ai"


def test_single_file_provider_with_inline_config(make_ctx, world):
    class NovaAdapter(BaseAdapter):
        id = "nova"
        name = "Nova"
        DEFAULT_CONFIG = {
            "urls": {"home": "https://nova.example/"},
            "selectors": {"logged_in": "#me", "chat_input": "textarea", "send_button": "#send",
                          "assistant_message": ".bot"},
            "manual_steps": {"import_memory": "Paste into Nova."},
        }

    register(NovaAdapter)
    try:
        assert "nova" in adapters.available()
        world["nova"] = FakeAI(memory=["Lives in Austin."])
        ad = get_adapter("nova", make_ctx())
        assert len(ad.export_memory()) == 1 and ad.last_method is Method.ASK_AI
        doc = MemoryDoc("x")
        doc.add("profile", Fact("Likes tea.", "x"))
        assert ad.import_memory(doc).method is Method.ASK_AI
        assert ad.config.host_allowed("nova.example")
    finally:
        adapters._REGISTRY.pop("nova", None)
