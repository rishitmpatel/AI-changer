"""Render memory for humans (memory.md) and for each target's preferred input form.

Import styles (chosen per target in its selectors.yaml ``import.style``):

* ``block``    – one markdown summary block grouped by category (Claude/Gemini import boxes).
* ``lines``    – one short fact per entry (saved-info / memory lists that take single items).
* ``remember`` – chat messages starting "Please remember …" (assistants that learn memory from chat).
* ``profile``  – compact "About me" prose for custom-instruction / profile fields with tight limits.
"""
from __future__ import annotations

from .schema import CATEGORIES, ChatArchive, Fact, MemoryDoc

TITLES = {"profile": "Profile", "preferences": "Preferences", "people": "People",
          "projects": "Projects", "topics": "Topics"}


def memory_markdown(doc: MemoryDoc) -> str:
    lines = [f"# Memory exported from {doc.source_provider}", "",
             f"Generated {doc.generated_at} · {len(doc)} facts", ""]
    for cat in CATEGORIES:
        facts = doc.categories.get(cat, [])
        if not facts:
            continue
        lines += [f"## {TITLES[cat]}", ""]
        lines += [f"- [{f.date}] {f.text}  <!-- {f.id} · {f.source_provider} -->" for f in facts]
        lines.append("")
    return "\n".join(lines)


def chats_markdown(chats: ChatArchive) -> str:
    out = [f"# Chat history exported from {chats.source_provider}", "",
           f"{len(chats)} conversations · {chats.message_count()} messages", ""]
    for conv in chats.conversations:
        out += [f"## {conv.title}", "", f"_id {conv.id} · created {conv.created or 'unknown'}_", ""]
        for m in conv.messages:
            who = {"user": "**You**", "assistant": "**Assistant**"}.get(m.role, "**System**")
            out += [f"{who}: {m.text}", ""]
    return "\n".join(out)


def fact_line(f: Fact, max_len: int | None = None) -> str:
    text = f.text.rstrip()
    if not text.endswith((".", "!", "?")):
        text += "."
    if max_len and len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def render_block(doc: MemoryDoc, header: bool = True) -> str:
    lines: list[str] = []
    if header:
        lines += ["Here is what my previous assistant knew about me. Please remember these details:", ""]
    for cat in CATEGORIES:
        facts = doc.categories.get(cat, [])
        if not facts:
            continue
        lines.append(f"## {TITLES[cat]}")
        lines += [f"- {fact_line(f)}" for f in facts]
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_lines(doc: MemoryDoc, max_len: int | None = None) -> list[str]:
    return [fact_line(f, max_len) for f in doc.all_facts()]


def render_profile(doc: MemoryDoc) -> str:
    parts = []
    for cat in CATEGORIES:
        facts = doc.categories.get(cat, [])
        if facts:
            parts.append(f"{TITLES[cat]}: " + " ".join(fact_line(f) for f in facts))
    return "\n".join(parts)


def chunk_text(text: str, limit: int, prefix: str = "", continued: str = "") -> list[str]:
    """Split ``text`` on line boundaries so every part (incl. prefix) fits within ``limit`` chars."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    budget = limit - len(prefix) - len(continued)
    if budget < 20:
        raise ValueError(f"limit {limit} too small for prefix")
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > budget:  # a single over-long line: hard split on a space
            cut = line.rfind(". ", 0, budget) + 1  # keep whole sentences (= whole facts) together
            if cut <= 0:
                cut = line.rfind(" ", 0, budget)
            if cut <= 0:
                cut = budget
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:cut].rstrip() + "\n")
            line = line[cut:].lstrip()
        if len(current) + len(line) > budget:
            parts.append(current)
            current = ""
        current += line
    if current.strip():
        parts.append(current)
    out = []
    for i, p in enumerate(parts):
        head = prefix if i == 0 else (continued or prefix)
        out.append((head + p).rstrip() + "\n")
    return out


def build_import_parts(doc: MemoryDoc, style: str, limit: int, item_limit: int | None = None) -> list[str]:
    """Rewrite facts in the target's preferred form and split into parts that fit its input box."""
    if style == "lines":
        return [line for line in render_lines(doc, item_limit or limit)]
    if style == "lines_block":  # a plain list, one fact per line, as many lines as fit per part
        return chunk_text("\n".join(f"- {line}" for line in render_lines(doc)), limit)
    if style == "remember":
        prefix = "Please remember the following facts about me for future conversations:\n"
        cont = "Please also remember these facts about me:\n"
        body = "\n".join(f"- {line}" for line in render_lines(doc))
        return chunk_text(body, limit, prefix=prefix, continued=cont)
    if style == "profile":
        return chunk_text(render_profile(doc), limit)
    if style == "primer":
        prefix = ("Context about me from my previous assistant. Use it in this conversation; "
                  "reply only with 'Noted'.\n")
        return chunk_text(render_block(doc, header=False), limit, prefix=prefix)
    # default: block
    return chunk_text(render_block(doc, header=True), limit,
                      continued="(continued) More details about me to remember:\n")
