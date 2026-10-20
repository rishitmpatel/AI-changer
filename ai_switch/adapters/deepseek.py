"""DeepSeek.

* Export file: Settings → Data → Export data (zip of JSON in the ChatGPT-like ``mapping``
  layout with ``fragments``); also available on request via service@deepseek.com.
* Memory: DeepSeek has no cross-chat memory. As a source we derive facts from your chats;
  as a target we send a context primer at the start of a new chat (it only lives in that chat).
"""
from ..extract import mine_facts_from_chats
from ..errors import MethodFailed
from ..schema import MemoryDoc
from .base import BaseAdapter, Method


class DeepSeekAdapter(BaseAdapter):
    id = "deepseek"
    name = "DeepSeek"
    has_memory = False
    export_memory_methods = (Method.EXPORT_FILE, Method.BROWSER, Method.CLIPBOARD)
    import_memory_methods = (Method.ASK_AI, Method.CLIPBOARD)
    import_chats_methods = (Method.CLIPBOARD,)

    def _export_memory_browser(self) -> MemoryDoc:
        chats = self._export_chats_browser()
        doc = mine_facts_from_chats(chats, self.id)
        if not len(doc):
            raise MethodFailed("no personal facts found in your recent DeepSeek chats")
        return doc
