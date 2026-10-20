"""Claude (Anthropic).

* Export file: Settings → Privacy → Export data → emailed zip with ``conversations.json``
  (``chat_messages``), ``users.json``, ``projects.json`` and ``memories.json``
  (``conversations_memory`` markdown + ``project_memories``).
* Official flow: Settings → Memory → "Start import" → paste → "Add to memory" (legacy UI:
  Settings → Capabilities). Reading: Settings → Memory shows the memory summary.
* Chat history: no official import; chats are uploaded as files to a Project instead.
"""
from ..exports import parse_claude
from .base import BaseAdapter, Method


class ClaudeAdapter(BaseAdapter):
    id = "claude"
    name = "Claude"
    import_memory_methods = (Method.OFFICIAL_FLOW, Method.ASK_AI, Method.BROWSER, Method.CLIPBOARD)
    import_chats_methods = (Method.OFFICIAL_FLOW, Method.CLIPBOARD)

    def parse_export(self, bundle):
        return parse_claude(bundle)
