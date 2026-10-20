"""ChatGPT (OpenAI).

* Export file: Settings → Data controls → Export data → emailed zip with ``conversations.json``
  (``mapping`` tree per chat), ``user.json``, ``chat.html``…  Saved memories are not documented
  as part of it, so memory usually comes from method 2 or 3.
* Official flow: Settings → Personalization → Manage memories (read the list).
* Import: there is no official memory import; ChatGPT saves memories when asked in chat
  ("Please remember …"), and custom instructions take a short "About you" text.
"""
from ..exports import parse_chatgpt
from .base import BaseAdapter, Method


class ChatGPTAdapter(BaseAdapter):
    id = "chatgpt"
    name = "ChatGPT"
    import_memory_methods = (Method.ASK_AI, Method.BROWSER, Method.CLIPBOARD)
    import_chats_methods = (Method.CLIPBOARD,)

    def parse_export(self, bundle):
        return parse_chatgpt(bundle)
