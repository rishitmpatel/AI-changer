"""Mistral Le Chat (being renamed "Mistral Vibe" in 2026).

* Memory: Intelligence → Memories (beta, Free plan included) — add, edit, delete, and
  "import from ChatGPT" (paste). Export by reading that list.
* Export file: no documented self-serve chat export for consumers; generic parser for any file.
"""
from .base import BaseAdapter, Method


class MistralAdapter(BaseAdapter):
    id = "mistral"
    name = "Mistral Le Chat"
    import_memory_methods = (Method.OFFICIAL_FLOW, Method.ASK_AI, Method.BROWSER, Method.CLIPBOARD)
    import_chats_methods = (Method.CLIPBOARD,)
