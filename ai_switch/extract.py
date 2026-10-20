"""Turn free text (an AI's memory dump, a settings page, a memories.json blob) into facts.

Also contains the keyword categoriser and a conservative miner that derives
facts from the user's own chat messages when a provider has no memory export.
"""
from __future__ import annotations

import re
from typing import Iterable

from .schema import CATEGORIES, ChatArchive, Fact, MemoryDoc, normalize_text, to_date, today

BEGIN_MARK = "BEGIN-MEMORY"
END_MARK = "END-MEMORY"

# Prompt sent to the *source* AI when we ask it to print what it remembers
# (method 3). It mirrors the official export prompts published by Anthropic
# and Google, plus markers so the reply can be captured reliably.
ASK_MEMORY_PROMPT = f"""I'm moving to another assistant and need a complete export of everything you remember about me.
List every stored memory, saved fact, custom instruction and detail you have learned about me from past conversations.
Rules:
- Write the memories verbatim where possible; do not summarise, invent or skip anything.
- Group them under exactly these headings: ## Profile, ## Preferences, ## People, ## Projects, ## Topics
- One memory per line, formatted as: - [YYYY-MM-DD] memory text   (use the date it was saved if you know it, otherwise leave the brackets out)
- Put the whole answer inside one code block, starting with the line {BEGIN_MARK} and ending with the line {END_MARK}.
If you remember nothing, reply with {BEGIN_MARK} and {END_MARK} and nothing between them."""

VERIFY_PROMPT = "What do you know about me? List everything you remember, one item per line."

_HEADING_TO_CATEGORY = {
    "profile": "profile", "about": "profile", "personal": "profile", "identity": "profile", "background": "profile",
    "work": "profile", "career": "profile", "demographic": "profile", "basic": "profile", "bio": "profile",
    "preference": "preferences", "instruction": "preferences", "style": "preferences", "likes": "preferences",
    "communication": "preferences", "response": "preferences", "format": "preferences", "habit": "preferences",
    "people": "people", "relationship": "people", "family": "people", "friends": "people", "contacts": "people",
    "project": "projects", "goal": "projects", "ongoing": "projects", "task": "projects", "plan": "projects",
    "topic": "topics", "interest": "topics", "hobby": "topics", "hobbies": "topics", "learning": "topics",
    "other": "topics", "misc": "topics", "knowledge": "topics", "skill": "topics",
}

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("people", re.compile(
        r"\b(wife|husband|partner|girlfriend|boyfriend|spouse|son|daughter|kids?|children|child|mother|mom|mum|"
        r"father|dad|brother|sister|sibling|friend|boss|manager|colleague|coworker|co-worker|cousin|aunt|uncle|"
        r"grand(?:mother|father|ma|pa)|fianc[ée]e?|roommate|mentor|team ?mate|dog|cat|pet)\b", re.I)),
    ("projects", re.compile(
        r"\b(project|working on|building|startup|side[- ]project|thesis|dissertation|app\b|repo|codebase|"
        r"launch(?:ing)?|deadline|roadmap|prototype|mvp|business plan|writing a (?:book|novel)|portfolio|"
        r"migrat\w+|planning to|goal is|aims? to)\b", re.I)),
    ("preferences", re.compile(
        r"\b(prefers?|preferred|preference|likes?|loves?|enjoys?|dislikes?|hates?|favou?rite|wants? "
        r"(?:responses|answers|you)|rather|avoid|vegetarian|vegan|allergic|style|tone|concise|brief|detailed|"
        r"bullet|format|metric|imperial|always|never|please (?:use|don't|do not))\b", re.I)),
    ("profile", re.compile(
        r"\b(name is|named|called|i am|i'm|is a|works? (?:as|at|for)|job|role|profession|occupation|engineer|"
        r"developer|designer|student|teacher|doctor|nurse|lawyer|lives? in|based in|located in|from|moved to|"
        r"born|age|years old|birthday|nationality|speaks?|native|language|time ?zone|degree|graduated|"
        r"university|college|school)\b", re.I)),
    ("topics", re.compile(
        r"\b(interested in|interest|hobby|hobbies|learning|studying|reads?|reading|plays?|playing|follows?|"
        r"fan of|curious|researching|practi[cs]es?|into)\b", re.I)),
]


def categorize(text: str) -> str:
    """Pick the best category for a fact using keyword rules (first match wins)."""
    for category, pattern in _RULES:
        if pattern.search(text):
            return category
    return "topics"


def heading_category(heading: str) -> str | None:
    words = re.findall(r"[a-z]+", heading.lower())
    for w in words:
        for key, cat in _HEADING_TO_CATEGORY.items():
            if w == key or w.rstrip("s") == key.rstrip("s"):
                return cat
    return None


_BULLET_RE = re.compile(r"^\s*(?:[-*•·◦‣▪]|\d{1,3}[.)]|\[\s?[x ]?\s?\])\s+(.*)$")
_DATE_PREFIX_RE = re.compile(
    r"^\s*[\[(]?\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4})\s*[\])]?\s*[-–—:]?\s*",
    re.I)
_NOISE_RE = re.compile(
    r"^(here(?:'s| is| are)|sure|certainly|of course|okay|ok|below|i (?:remember|have stored|don't have)|"
    r"this is|these are|let me know|hope this|```|" + BEGIN_MARK + "|" + END_MARK + r")", re.I)


def capture_between_markers(reply: str) -> str:
    """Return the text between BEGIN-MEMORY and END-MEMORY if present, else the whole reply."""
    start = reply.find(BEGIN_MARK)
    end = reply.rfind(END_MARK)
    if start != -1 and end > start:
        return reply[start + len(BEGIN_MARK):end]
    if start != -1:
        return reply[start + len(BEGIN_MARK):]
    return reply


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if p.strip()]


def parse_memory_text(text: str, provider: str, origin: str, default_date: str | None = None,
                      split_paragraphs: bool = True) -> MemoryDoc:
    """Parse a memory dump (markdown bullets, headings, dated lines or prose) into a MemoryDoc."""
    doc = MemoryDoc(provider)
    body = capture_between_markers(text)
    flat = body.strip()
    if "\n" not in flat and flat.startswith(("- ", "* ")) and flat.count(" - ") >= 1:
        # some pages flatten list markup into one line: "- a - b - c"
        body = "\n".join("- " + p for p in re.split(r"\s+[-*]\s+", flat[2:]) if p.strip())
    current_heading_cat: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^(#{1,6}\s+|\*\*)(.+?)(\*\*)?:?\s*$", line)
        if heading and not _BULLET_RE.match(line):
            current_heading_cat = heading_category(heading.group(2))
            continue
        if line.endswith(":") and len(line) < 50 and not _BULLET_RE.match(line):
            cat = heading_category(line)
            if cat:
                current_heading_cat = cat
                continue
        m = _BULLET_RE.match(line)
        items = [m.group(1)] if m else (_split_sentences(line) if split_paragraphs else [line])
        for item in items:
            item = item.strip().strip("`")
            date = default_date
            dm = _DATE_PREFIX_RE.match(item)
            if dm:
                date = to_date(dm.group(1)) or default_date
                item = item[dm.end():]
            item = re.sub(r"^\*\*(.+?)\*\*:?\s*", r"\1: ", item).strip()
            item = item.strip(" -–—").strip()
            if len(item) < 3 or _NOISE_RE.match(item):
                continue
            # Specific headings (Preferences, People, …) win; a broad "profile/personal" heading
            # lets the keyword rules move e.g. a partner or a project to the right category.
            guessed = categorize(item)
            if current_heading_cat and current_heading_cat != "profile":
                cat = current_heading_cat
            elif current_heading_cat == "profile" and guessed in ("people", "projects"):
                cat = guessed
            else:
                cat = current_heading_cat or guessed
            doc.add(cat, Fact(text=item, source_provider=provider, date=date or today(),
                              origin=origin))
    return doc


def facts_from_strings(items: Iterable[tuple[str, str | None]], provider: str, origin: str) -> MemoryDoc:
    """Build a MemoryDoc from (text, date) pairs, e.g. a provider's list of saved memories."""
    doc = MemoryDoc(provider)
    for text, date in items:
        text = normalize_text(text)
        if len(text) < 3:
            continue
        kwargs = {"date": date} if date else {}
        doc.add(categorize(text), Fact(text=text, source_provider=provider, origin=origin, **kwargs))
    return doc


# --------------------------------------------------------------------------- mining chats

_SELF_PATTERNS = [
    re.compile(r"\bmy name is ([A-Z][\w'-]+(?: [A-Z][\w'-]+)?)", re.I),
    re.compile(r"\bI(?:'m| am) (?:a|an) ([\w -]{3,60}?)(?:[.,!;]| and | who | at |$)", re.I),
    re.compile(r"\bI work (?:as|at|for) ([\w &,.-]{2,60}?)(?:[.!;]| and |$)", re.I),
    re.compile(r"\bI live in ([\w ,'-]{2,50}?)(?:[.!;]| and |$)", re.I),
    re.compile(r"\bI(?:'m| am) (?:based|located) in ([\w ,'-]{2,50}?)(?:[.!;]| and |$)", re.I),
    re.compile(r"\bI prefer ([^.!?\n]{3,80})", re.I),
    re.compile(r"\bI(?:'m| am) (?:working on|building) ([^.!?\n]{3,80})", re.I),
    re.compile(r"\bmy (wife|husband|partner|son|daughter|boss|dog|cat)(?:'s name)? is ([^.!?\n]{2,40})", re.I),
    re.compile(r"\bremember that ([^.!?\n]{3,120})", re.I),
]
_TEMPLATES = [
    "User's name is {0}.", "User is a {0}.", "User works {verb} {0}.", "User lives in {0}.",
    "User is based in {0}.", "User prefers {0}.", "User is working on {0}.", "User's {0} is {1}.", "{0}",
]


def mine_facts_from_chats(chats: ChatArchive, provider: str, limit: int = 200) -> MemoryDoc:
    """Derive likely facts from the user's own messages. Used when an export has no memory section.

    Deliberately conservative: only first-person statements with explicit patterns.
    """
    doc = MemoryDoc(provider)
    for conv in chats.conversations:
        for msg in conv.messages:
            if msg.role != "user" or not msg.text:
                continue
            for idx, pattern in enumerate(_SELF_PATTERNS):
                for m in pattern.finditer(msg.text):
                    groups = [g.strip(" ,") for g in m.groups()]
                    if idx == 2:
                        verb = re.search(r"work (as|at|for)", m.group(0), re.I).group(1).lower()
                        text = _TEMPLATES[idx].format(groups[0], verb=verb)
                    else:
                        text = _TEMPLATES[idx].format(*groups, verb="")
                    if text[0].islower():
                        text = text[0].upper() + text[1:]
                    if not text.endswith("."):
                        text += "."
                    date = to_date(msg.time) or to_date(conv.created)
                    kwargs = {"date": date} if date else {}
                    doc.add(categorize(text), Fact(text=text, source_provider=provider,
                                                   origin="derived_from_chats", **kwargs))
                    if len(doc) >= limit:
                        return doc
    return doc
