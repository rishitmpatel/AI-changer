"""Microsoft Copilot (consumer, personal Microsoft account).

* Export file: account.microsoft.com/privacy → Copilot → "Export all activity history" → CSV
  (prompts + responses). No memory section, so facts are derived from chats if nothing else works.
* Memory: Settings → Personalization → memory list (view/delete); Copilot also stores memories
  when asked "Remember that …" in chat — that is how we import.
* Microsoft 365 (work) Copilot memory is admin-managed (eDiscovery) and out of scope.
"""
from ..exports import ExportBundle, ParsedExport, parse_csv_chats, parse_generic
from ..schema import ChatArchive, MemoryDoc
from .base import BaseAdapter, Method


class CopilotAdapter(BaseAdapter):
    id = "copilot"
    name = "Microsoft Copilot"
    export_memory_methods = (Method.EXPORT_FILE, Method.OFFICIAL_FLOW, Method.ASK_AI, Method.CLIPBOARD)
    import_memory_methods = (Method.ASK_AI, Method.CLIPBOARD)
    import_chats_methods = (Method.CLIPBOARD,)

    def parse_export(self, bundle: ExportBundle) -> ParsedExport:
        csvs = [n for n in bundle.names() if n.lower().endswith(".csv")]
        if csvs:
            chats = ChatArchive(self.id)
            for name in csvs:
                chats.conversations += parse_csv_chats(bundle.read_text(name), self.id)
            return ParsedExport(MemoryDoc(self.id), chats)
        return parse_generic(bundle, self.id)
