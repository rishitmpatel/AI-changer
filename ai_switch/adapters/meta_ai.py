"""Meta AI (meta.ai, Meta AI app, WhatsApp/Messenger/Instagram).

* Export file: Accounts Center → Your information and permissions → Download your information
  (JSON). Meta encodes UTF-8 text as latin-1 escapes; the parser repairs that.
* Memory: profile → Memory (view, swipe to delete, Manage). Meta AI saves details when told
  "Remember that …" in chat, which is how we import. No bulk memory import.
"""
from .base import BaseAdapter, Method


class MetaAIAdapter(BaseAdapter):
    id = "meta_ai"
    name = "Meta AI"
    export_memory_methods = (Method.EXPORT_FILE, Method.OFFICIAL_FLOW, Method.ASK_AI, Method.CLIPBOARD)
    import_memory_methods = (Method.ASK_AI, Method.CLIPBOARD)
    import_chats_methods = (Method.CLIPBOARD,)
