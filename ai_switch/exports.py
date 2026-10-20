"""Read official data-export files (zip, folder, json, csv, html) without extracting to disk.

Provider-specific parsers handle the documented layouts (ChatGPT ``conversations.json``
with a ``mapping`` tree, Claude ``conversations.json`` + ``memories.json``, Google
Takeout ``MyActivity.json``, Copilot privacy-dashboard CSV, DeepSeek ``fragments``).
:func:`parse_generic` is a tolerant fallback for undocumented or changed layouts.
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .errors import MethodFailed, MethodUnavailable
from .extract import facts_from_strings, parse_memory_text
from .schema import ChatArchive, Conversation, MemoryDoc, Message, to_date

MAX_MEMBER_BYTES = 1_500_000_000  # refuse absurd single files inside a zip
MAX_TOTAL_BYTES = 6_000_000_000


@dataclass
class ExportBundle:
    """Uniform read-only view over a zip, a folder or a single file."""

    path: Path
    _zip: zipfile.ZipFile | None = None
    _files: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def open(cls, path: Path | str) -> "ExportBundle":
        p = Path(path).expanduser()
        if not p.exists():
            raise MethodUnavailable(f"export file not found: {p}",
                                    hint="Check the path passed to --export (drag the file into the terminal to paste its path).")
        b = cls(p)
        if p.is_dir():
            b._files = {str(f.relative_to(p)).replace("\\", "/"): f for f in p.rglob("*") if f.is_file()}
        elif zipfile.is_zipfile(p):
            b._zip = zipfile.ZipFile(p)
            total = sum(i.file_size for i in b._zip.infolist())
            if total > MAX_TOTAL_BYTES:
                raise MethodFailed(f"export archive is unexpectedly large ({total // 2**20} MB uncompressed)",
                                   hint="Unzip it yourself and pass the folder to --export.")
        else:
            b._files = {p.name: p}
        return b

    def names(self) -> list[str]:
        if self._zip:
            return [i.filename for i in self._zip.infolist() if not i.is_dir() and i.file_size <= MAX_MEMBER_BYTES]
        return sorted(self._files)

    def read_bytes(self, name: str) -> bytes:
        if self._zip:
            return self._zip.read(name)
        return self._files[name].read_bytes()

    def read_text(self, name: str) -> str:
        data = self.read_bytes(name)
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def read_json(self, name: str) -> Any:
        try:
            return json.loads(self.read_text(name))
        except json.JSONDecodeError as exc:
            raise MethodFailed(f"{name} in the export is not valid JSON ({exc.msg} at line {exc.lineno})",
                               hint="Download the export again; the file may be truncated.") from exc

    def find(self, pattern: str) -> list[str]:
        rx = re.compile(pattern, re.I)
        return [n for n in self.names() if rx.search(n)]

    def close(self) -> None:
        if self._zip:
            self._zip.close()


@dataclass
class ParsedExport:
    memory: MemoryDoc
    chats: ChatArchive


# --------------------------------------------------------------------------- helpers

_USER_ROLES = {"user", "human", "request", "prompt", "me", "you", "customer", "question"}
_ASSISTANT_ROLES = {"assistant", "ai", "bot", "model", "response", "answer", "grok", "copilot", "chatgpt", "claude",
                    "gemini", "perplexity", "meta ai", "metaai", "deepseek", "mistral", "le chat", "lechat", "system_ai"}
_SKIP_ROLES = {"tool", "function", "think", "thinking", "reasoning", "search"}


def norm_role(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("role") or value.get("name") or value.get("type")
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in _USER_ROLES:
        return "user"
    if v in _ASSISTANT_ROLES:
        return "assistant"
    if v == "system":
        return "system"
    if v in _SKIP_ROLES:
        return None
    return None


def strip_html(text: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def fix_mojibake(text: str) -> str:
    """Meta 'Download your information' JSON stores UTF-8 bytes as latin-1 escapes."""
    if not re.search(r"[Â-ô][\u0080-¿]", text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def content_text(content: Any) -> str:
    """Flatten the many shapes message content takes across exports."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float)):
        return str(content)
    if isinstance(content, list):
        return "\n".join(t for t in (content_text(c) for c in content) if t)
    if isinstance(content, dict):
        ctype = str(content.get("type") or content.get("content_type") or "").lower()
        if ctype in ("thinking", "reasoning", "tool_use", "tool_result", "image", "image_asset_pointer"):
            return ""
        for key in ("parts", "text", "content", "message", "body", "value", "markdown", "html"):
            if key in content and content[key] not in (None, ""):
                t = content_text(content[key])
                return strip_html(t) if key == "html" else t
    return ""


def _date_of(d: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            v = d[k]
            if isinstance(v, dict):  # {"$date": ...} (Mongo-style exports)
                v = v.get("$date") or v.get("$numberLong") or next(iter(v.values()), None)
                if isinstance(v, dict):
                    v = v.get("$numberLong")
            got = to_date(v)
            if got:
                return got
    return None


# --------------------------------------------------------------------------- ChatGPT


def parse_chatgpt_conversations(data: Any, provider: str = "chatgpt") -> list[Conversation]:
    convs = []
    items = data if isinstance(data, list) else data.get("conversations", []) if isinstance(data, dict) else []
    for i, c in enumerate(items):
        mapping = c.get("mapping") or {}
        node_id = c.get("current_node")
        chain = []
        seen = set()
        if node_id and node_id in mapping:
            while node_id and node_id in mapping and node_id not in seen:
                seen.add(node_id)
                chain.append(mapping[node_id])
                node_id = mapping[node_id].get("parent")
            chain.reverse()
        else:  # no current_node: fall back to insertion order
            chain = list(mapping.values())
        msgs = []
        for node in chain:
            m = node.get("message") or {}
            if not m:
                continue
            if m.get("fragments"):  # DeepSeek uses the same tree with typed 'fragments'
                for frag in m["fragments"]:
                    frole = norm_role(frag.get("type"))
                    text = content_text(frag.get("content"))
                    if frole and text.strip():
                        msgs.append(Message(frole, text.strip(), _date_of(m, "inserted_at", "create_time")))
                continue
            role = norm_role((m.get("author") or {}).get("role"))
            meta = m.get("metadata") or {}
            if role is None or meta.get("is_visually_hidden_from_conversation"):
                continue
            text = content_text(m.get("content"))
            if text.strip():
                msgs.append(Message(role, text.strip(), _date_of(m, "create_time", "inserted_at")))
        if not msgs:
            continue
        convs.append(Conversation(
            id=str(c.get("conversation_id") or c.get("id") or f"{provider}-{i}"),
            title=c.get("title") or "Untitled", source_provider=provider, messages=msgs,
            created=_date_of(c, "create_time", "inserted_at"), updated=_date_of(c, "update_time", "updated_at")))
    return convs


def parse_chatgpt(bundle: ExportBundle) -> ParsedExport:
    memory = MemoryDoc("chatgpt")
    chats = ChatArchive("chatgpt")
    conv_files = sorted(bundle.find(r"(^|/)conversations(-\d+)?\.json$"))
    for name in conv_files:
        chats.conversations += parse_chatgpt_conversations(bundle.read_json(name))
    # Memories are not documented as part of the export; newer exports or user-added files may carry them.
    for name in bundle.find(r"memor(y|ies)[^/]*\.json$"):
        memory.extend(memory_from_any(bundle.read_json(name), "chatgpt", "export_file"))
    for name in bundle.find(r"(custom_instructions|user_system_messages|user_settings|user)\.json$"):
        memory.extend(memory_from_any(bundle.read_json(name), "chatgpt", "export_file"))
    if not conv_files and not len(memory):
        raise MethodFailed("this does not look like a ChatGPT export (no conversations.json)",
                           hint="Use the zip from ChatGPT → Settings → Data controls → Export data.")
    return ParsedExport(memory, chats)


def write_chatgpt_zip(chats: ChatArchive, path: Path) -> Path:
    """Write chats in ChatGPT's export layout (accepted by Gemini's chat-history import)."""
    out = []
    for c in chats.conversations:
        mapping: dict[str, Any] = {"root": {"id": "root", "message": None, "parent": None, "children": []}}
        parent = "root"
        for i, m in enumerate(c.messages):
            nid = f"{c.id}-{i}"
            mapping[nid] = {"id": nid, "parent": parent, "children": [], "message": {
                "id": nid, "author": {"role": m.role}, "create_time": None,
                "content": {"content_type": "text", "parts": [m.text]}}}
            mapping[parent]["children"].append(nid)
            parent = nid
        out.append({"title": c.title, "create_time": None, "update_time": None, "mapping": mapping,
                    "current_node": parent, "conversation_id": c.id, "id": c.id})
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("conversations.json", json.dumps(out, ensure_ascii=False))
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover
        pass
    return path


# --------------------------------------------------------------------------- Claude


def parse_claude(bundle: ExportBundle) -> ParsedExport:
    memory = MemoryDoc("claude")
    chats = ChatArchive("claude")
    for name in bundle.find(r"(^|/)conversations\.json$"):
        for i, c in enumerate(bundle.read_json(name) or []):
            msgs = []
            for m in c.get("chat_messages", []):
                role = norm_role(m.get("sender"))
                text = m.get("text") or content_text(m.get("content"))
                if role and text and text.strip():
                    msgs.append(Message(role, text.strip(), _date_of(m, "created_at")))
            if msgs:
                chats.conversations.append(Conversation(
                    id=str(c.get("uuid") or f"claude-{i}"), title=c.get("name") or "Untitled",
                    source_provider="claude", messages=msgs,
                    created=_date_of(c, "created_at"), updated=_date_of(c, "updated_at")))
    for name in bundle.find(r"(^|/)memories\.json$"):
        data = bundle.read_json(name)
        for entry in data if isinstance(data, list) else [data]:
            if not isinstance(entry, dict):
                continue
            if entry.get("conversations_memory"):
                memory.extend(parse_memory_text(entry["conversations_memory"], "claude", "export_file"))
            for proj_text in (entry.get("project_memories") or {}).values():
                if isinstance(proj_text, str):
                    pm = parse_memory_text(proj_text, "claude", "export_file")
                    for _, f in pm.iter_facts():
                        memory.add("projects", f)
    for name in bundle.find(r"(^|/)users\.json$"):
        users = bundle.read_json(name)
        for u in users if isinstance(users, list) else [users]:
            if isinstance(u, dict) and u.get("full_name"):
                memory.extend(facts_from_strings([(f"User's name is {u['full_name']}.", None)], "claude", "export_file"))
    if not len(chats) and not len(memory):
        raise MethodFailed("this does not look like a Claude export (no conversations.json / memories.json)",
                           hint="Use the zip from Claude → Settings → Privacy → Export data.")
    return ParsedExport(memory, chats)


# --------------------------------------------------------------------------- Gemini (Google Takeout)


def parse_gemini(bundle: ExportBundle) -> ParsedExport:
    memory = MemoryDoc("gemini")
    chats = ChatArchive("gemini")
    by_day: dict[str, Conversation] = {}
    json_files = [n for n in bundle.find(r"MyActivity\.json$") if re.search(r"gemini|bard", n, re.I)] \
        or bundle.find(r"MyActivity\.json$")
    for name in json_files:
        for e in bundle.read_json(name) or []:
            products = " ".join(e.get("products", [])) + " " + str(e.get("header", ""))
            if not re.search(r"gemini|bard", products, re.I):
                continue
            title = str(e.get("title", ""))
            prompt = re.sub(r"^(Prompted|Asked|Said)\s+", "", title).strip()
            if not prompt or title.startswith(("Used", "Visited", "Gave feedback", "Selected", "Created Gem")):
                continue
            day = to_date(e.get("time")) or "unknown"
            conv = by_day.setdefault(day, Conversation(id=f"gemini-{day}", title=f"Gemini activity {day}",
                                                       source_provider="gemini", created=day, updated=day))
            conv.messages.append(Message("user", prompt, day))
            reply = "\n".join(strip_html(i.get("html", "")) for i in e.get("safeHtmlItem", []) if i.get("html"))
            if reply:
                conv.messages.append(Message("assistant", reply, day))
    for name in bundle.find(r"MyActivity\.html$"):
        if json_files:
            break
        page = bundle.read_text(name)
        for block in re.findall(r'<div class="outer-cell.*?</div>\s*</div>\s*</div>', page, re.S):
            text = strip_html(block)
            m = re.match(r"(?:Gemini Apps|Bard)\s*Prompted\s+(.*)", text, re.S)
            if m:
                conv = by_day.setdefault("html", Conversation(id="gemini-html", title="Gemini activity",
                                                              source_provider="gemini"))
                conv.messages.append(Message("user", m.group(1).strip()))
    # newest Takeout lists conversations newest-first; keep chronological order inside a day
    for conv in by_day.values():
        chats.conversations.append(conv)
    for name in bundle.find(r"(saved[_ -]?info|memor(y|ies))[^/]*\.(json|txt|md)$"):
        if name.endswith(".json"):
            memory.extend(memory_from_any(bundle.read_json(name), "gemini", "export_file"))
        else:
            memory.extend(parse_memory_text(bundle.read_text(name), "gemini", "export_file"))
    if not len(chats) and not len(memory):
        raise MethodFailed("no Gemini Apps activity found in this export",
                           hint="In Google Takeout pick 'My Activity' → only 'Gemini Apps', format JSON. "
                                "Activity must be turned on for history to exist.")
    return ParsedExport(memory, chats)


# --------------------------------------------------------------------------- CSV (Copilot)


def parse_csv_chats(text: str, provider: str) -> list[Conversation]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    cols = {c.lower().strip(): c for c in reader.fieldnames}

    def col(*names: str) -> str | None:
        for n in names:
            for low, orig in cols.items():
                if n in low:
                    return orig
        return None

    c_conv = col("conversation", "thread", "session", "chat id", "title")
    c_time = col("time", "date", "created")
    c_role = col("author", "role", "sender", "speaker", "from")
    c_text = col("message", "text", "content", "body")
    c_prompt, c_resp = col("prompt", "request", "question"), col("response", "answer", "reply")
    convs: dict[str, Conversation] = {}
    for i, row in enumerate(reader):
        key = (row.get(c_conv) if c_conv else None) or "copilot"
        conv = convs.setdefault(key, Conversation(id=f"{provider}-{len(convs)}", title=key[:80],
                                                  source_provider=provider))
        when = to_date(row.get(c_time)) if c_time else None
        conv.created = conv.created or when
        conv.updated = when or conv.updated
        if c_role and c_text and c_text not in (c_prompt, c_resp):
            role = norm_role(row.get(c_role)) or ("user" if i % 2 == 0 else "assistant")
            if (row.get(c_text) or "").strip():
                conv.messages.append(Message(role, row[c_text].strip(), when))
        else:
            if c_prompt and (row.get(c_prompt) or "").strip():
                conv.messages.append(Message("user", row[c_prompt].strip(), when))
            if c_resp and (row.get(c_resp) or "").strip():
                conv.messages.append(Message("assistant", row[c_resp].strip(), when))
    return [c for c in convs.values() if c.messages]


# --------------------------------------------------------------------------- generic


_MEMORY_KEYS = re.compile(r"^(memories|memory|saved_?info|user_?memories|conversations_memory|about_user_message|"
                          r"about_model_message|custom_?instructions|traits|bio|user_profile|personalization|"
                          r"what_.*_know|facts)$", re.I)
_TEXT_KEYS = ("text", "content", "memory", "value", "fact", "body", "message", "description")


def memory_from_any(data: Any, provider: str, origin: str) -> MemoryDoc:
    """Pull memory-like content (lists of saved facts or memory text) out of arbitrary JSON."""
    doc = MemoryDoc(provider)

    def from_value(value: Any, key: str = "") -> None:
        if isinstance(value, str) and value.strip():
            sub = parse_memory_text(fix_mojibake(value), provider, origin)
            if key.lower() in ("about_model_message", "custom_instructions") and len(sub):
                for _, f in list(sub.iter_facts()):
                    doc.add("preferences", f)
            else:
                doc.extend(sub)
        elif isinstance(value, list):
            pairs = []
            for item in value:
                if isinstance(item, str):
                    pairs.append((fix_mojibake(item), None))
                elif isinstance(item, dict):
                    txt = next((item[k] for k in _TEXT_KEYS if isinstance(item.get(k), str) and item[k].strip()), None)
                    if txt:
                        pairs.append((fix_mojibake(txt), _date_of(item, "created_at", "createdAt", "create_time", "inserted_at",
                                                                   "updated_at", "date", "timestamp", "timestamp_ms", "time")))
                    else:
                        walk(item)
            doc.extend(facts_from_strings(pairs, provider, origin))
        elif isinstance(value, dict):
            walk(value)

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if _MEMORY_KEYS.match(str(k)):
                    from_value(v, str(k))
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    if isinstance(data, list) and data and all(isinstance(x, (str, dict)) for x in data):
        # a bare list at top level of a memory file = list of memories
        sample = data[0]
        if isinstance(sample, str) or any(k in sample for k in _TEXT_KEYS):
            from_value(data)
            return doc
    walk(data)
    return doc


def _message_from(d: dict[str, Any]) -> Message | None:
    for wrapper in ("response", "message", "item", "node"):  # unwrap {"response": {...}}
        inner = d.get(wrapper)
        if isinstance(inner, dict) and len(d) <= 3 and any(k in inner for k in ("sender", "role", "author")):
            d = inner
    role = None
    for key in ("role", "sender", "author", "from", "speaker", "type", "participant", "is_user", "isUser"):
        if key in d:
            raw = d[key]
            if key in ("is_user", "isUser"):
                role = "user" if raw else "assistant"
            else:
                role = norm_role(raw)
            if role:
                break
    if not role:
        return None
    text = ""
    for key in ("text", "content", "message", "body", "parts", "fragments", "markdown", "html"):
        if key in d:
            text = content_text(d[key])
            if text:
                break
    if not text.strip():
        return None
    return Message(role, fix_mojibake(text.strip()),
                   _date_of(d, "create_time", "created_at", "createdAt", "timestamp", "timestamp_ms", "time", "inserted_at", "date"))


def _looks_like_messages(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    dicts = [v for v in value[:20] if isinstance(v, dict)]
    return bool(dicts) and sum(_message_from(v) is not None for v in dicts) >= max(1, len(dicts) // 2)


def parse_generic_chats(data: Any, provider: str) -> list[Conversation]:
    convs: list[Conversation] = []

    def visit(obj: Any, parent: dict[str, Any] | None) -> None:
        if isinstance(obj, dict):
            if "mapping" in obj and isinstance(obj["mapping"], dict):
                convs.extend(parse_chatgpt_conversations([obj], provider))
                return
            for k, v in obj.items():
                if _looks_like_messages(v):
                    owner = obj
                    meta = owner.get("conversation") if isinstance(owner.get("conversation"), dict) else owner
                    msgs = [m for m in (_message_from(x) for x in v if isinstance(x, dict)) if m]
                    convs.append(Conversation(
                        id=str(meta.get("id") or meta.get("uuid") or meta.get("conversation_id") or
                               f"{provider}-{len(convs)}"),
                        title=fix_mojibake(str(meta.get("title") or meta.get("name") or "Untitled")),
                        source_provider=provider, messages=msgs,
                        created=_date_of(meta, "create_time", "created_at", "createdAt", "inserted_at", "timestamp"),
                        updated=_date_of(meta, "update_time", "updated_at", "modify_time", "updatedAt")))
                elif isinstance(v, (dict, list)):
                    visit(v, obj)
        elif isinstance(obj, list):
            if _looks_like_messages(obj) and parent is None:
                msgs = [m for m in (_message_from(x) for x in obj if isinstance(x, dict)) if m]
                convs.append(Conversation(id=f"{provider}-0", title="Untitled", source_provider=provider, messages=msgs))
                return
            for item in obj:
                visit(item, None)

    visit(data, None)
    return [c for c in convs if c.messages]


def iter_json_files(bundle: ExportBundle) -> Iterator[tuple[str, Any]]:
    for name in bundle.names():
        if name.lower().endswith(".json") and not name.split("/")[-1].startswith("."):
            try:
                yield name, bundle.read_json(name)
            except MethodFailed:
                continue


def parse_generic(bundle: ExportBundle, provider: str) -> ParsedExport:
    """Tolerant parser: finds conversations and memory-like data anywhere in the export."""
    memory = MemoryDoc(provider)
    chats = ChatArchive(provider)
    for name, data in iter_json_files(bundle):
        memory.extend(memory_from_any(data, provider, "export_file"))
        if not re.search(r"memor|saved|custom|profile|personal", name, re.I):
            chats.conversations += parse_generic_chats(data, provider)
    for name in bundle.names():
        low = name.lower()
        if low.endswith(".csv"):
            chats.conversations += parse_csv_chats(bundle.read_text(name), provider)
        elif low.endswith((".md", ".txt")) and re.search(r"memor|saved", low):
            memory.extend(parse_memory_text(bundle.read_text(name), provider, "export_file"))
    if not len(chats) and not len(memory):
        raise MethodFailed(f"found no chats or memory in {bundle.path.name}",
                           hint="Make sure this is the provider's own data export. If the format changed, "
                                "open an issue with the file names (not the contents) in the zip.")
    return ParsedExport(memory, chats)
