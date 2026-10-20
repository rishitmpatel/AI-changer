"""Gemini (Google).

* Export file: Google Takeout → My Activity → Gemini Apps (JSON) → ``MyActivity.json``.
  Takeout has prompts/responses but not "Saved info", so memory comes from method 2/3.
* Official flow (since March 2026): Settings → Import memory (paste a summary) and
  Import chats (upload a .zip export from ChatGPT/Claude, up to 5 GB). Saved info page
  (gemini.google.com/saved-info) takes one item at a time.
* Not available for work/school accounts, under-18s, or in the EEA, Switzerland and the UK.
"""
from ..exports import parse_gemini
from .base import BaseAdapter, Method


class GeminiAdapter(BaseAdapter):
    id = "gemini"
    name = "Gemini"
    export_memory_methods = (Method.EXPORT_FILE, Method.OFFICIAL_FLOW, Method.ASK_AI, Method.CLIPBOARD)
    import_memory_methods = (Method.OFFICIAL_FLOW, Method.ASK_AI, Method.BROWSER, Method.CLIPBOARD)
    import_chats_methods = (Method.OFFICIAL_FLOW, Method.CLIPBOARD)

    def parse_export(self, bundle):
        return parse_gemini(bundle)
