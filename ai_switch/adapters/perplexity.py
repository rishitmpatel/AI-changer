"""Perplexity.

* Export file: no self-serve account export documented; a file from a data request is
  parsed with the generic parser.
* Memory: Settings → Personalize → Memory → Manage (view/delete). Profile box
  ("Introduce yourself") holds free text and is our structured import target.
"""
from .base import BaseAdapter, Method


class PerplexityAdapter(BaseAdapter):
    id = "perplexity"
    name = "Perplexity"
    import_memory_methods = (Method.ASK_AI, Method.BROWSER, Method.CLIPBOARD)
    import_chats_methods = (Method.CLIPBOARD,)
