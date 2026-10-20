"""Grok (xAI).

* Export file: grok.com → Settings → Data controls → Export account data (accounts.x.ai) →
  zip of JSON. Layout is undocumented, so the tolerant generic parser is used.
* Memory: on by default (not in parts of the EU/UK); no public view/export page → ask the AI.
* Import: "Import memory from other AI providers" was only seen in tests, so we use chat
  ("Please remember …") and the Customize / custom-instructions box.
"""
from .base import BaseAdapter, Method


class GrokAdapter(BaseAdapter):
    id = "grok"
    name = "Grok"
    import_memory_methods = (Method.OFFICIAL_FLOW, Method.ASK_AI, Method.BROWSER, Method.CLIPBOARD)
    import_chats_methods = (Method.CLIPBOARD,)
