"""Detect and remove personal identifiers before anything is sent to the target AI.

Values are only ever shown masked on screen; the full list goes to a local
privacy_review.md so the user can check it privately.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import ChatArchive, MemoryDoc

_STREET_SUFFIX = (r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl|"
                  r"terrace|close|crescent|square|sq|parkway|pkwy|highway|hwy|marg|nagar|chowk)")


@dataclass(frozen=True)
class Detector:
    kind: str
    label: str
    pattern: re.Pattern[str]
    check: object = None  # optional callable(str) -> bool for checksum validation


def _luhn_ok(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", value)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban_ok(value: str) -> bool:
    v = re.sub(r"\s", "", value).upper()
    if not 15 <= len(v) <= 34:
        return False
    rearranged = v[4:] + v[:4]
    num = "".join(str(int(c, 36)) for c in rearranged)
    return int(num) % 97 == 1


_VERHOEFF_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
               [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
               [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
               [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
               [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
               [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def _verhoeff_ok(value: str) -> bool:
    """Aadhaar numbers carry a Verhoeff check digit."""
    c = 0
    for i, ch in enumerate(reversed(re.sub(r"\D", "", value))):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def _phone_ok(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 9 <= len(digits) <= 15 and not re.fullmatch(r"(19|20)\d{2}(19|20)\d{2}", digits)


DETECTORS: list[Detector] = [
    Detector("email", "Email address", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I)),
    Detector("iban", "Bank account (IBAN)", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"),
             _iban_ok),
    Detector("card", "Payment card number", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _luhn_ok),
    Detector("ssn", "US Social Security number", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    Detector("aadhaar", "Aadhaar number", re.compile(r"\b[2-9]\d{3} \d{4} \d{4}\b"), _verhoeff_ok),
    Detector("pan", "PAN (India tax ID)", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    Detector("uk_nino", "UK National Insurance number", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b")),
    Detector("id_number", "ID / passport / licence number", re.compile(
        r"\b(?:passport|licen[cs]e|driver'?s licen[cs]e|national id|id number|id no\.?|employee id|"
        r"student id|member(?:ship)? (?:no|number)|tax id|ssn|nhs number)\s*(?:number|no\.?|#)?\s*(?:is|:|=)?\s*"
        r"([A-Z0-9][A-Z0-9-]{4,19})\b", re.I)),
    Detector("ip", "IP address", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    Detector("phone", "Phone number", re.compile(
        r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,5}\)[\s.-]?)?\d{2,5}(?:[\s.-]\d{2,5}){1,4}(?![\w-])"), _phone_ok),
    Detector("address", "Street address", re.compile(
        r"\b\d{1,6}[A-Za-z]?,?\s+(?:[A-Z][\w'-]*\s){1,4}" + _STREET_SUFFIX + r"\b\.?(?:,?\s+[A-Z][\w'-]+){0,3}"
        r"(?:,?\s+(?:[A-Z]{2}\s+)?\d{5}(?:-\d{4})?)?", re.I)),
    Detector("postcode", "Postal code", re.compile(
        r"\b(?:[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}|(?:zip|postcode|postal code|pin ?code)\s*:?\s*\d{5,6})\b", re.I)),
    Detector("dob", "Date of birth", re.compile(
        r"\b(?:born on|date of birth|dob|birthday is)\s*:?\s*([^.,;\n]{4,30})", re.I)),
]


@dataclass
class Finding:
    kind: str
    label: str
    value: str
    locations: list[str] = field(default_factory=list)  # fact ids / "chat:<conv id>"

    @property
    def masked(self) -> str:
        return mask(self.value, self.kind)


def mask(value: str, kind: str = "") -> str:
    v = value.strip()
    if kind == "email" and "@" in v:
        user, domain = v.split("@", 1)
        return (user[:1] + "***@" + domain[:1] + "***." + domain.rsplit(".", 1)[-1])
    if len(v) <= 4:
        return "*" * len(v)
    keep = 2 if len(v) < 10 else 3
    return v[:keep] + "*" * max(3, len(v) - 2 * keep) + v[-keep:]


def scan_text(text: str) -> list[tuple[Detector, str, tuple[int, int]]]:
    """Return (detector, value, span) for every identifier in ``text``, without overlaps."""
    hits: list[tuple[Detector, str, tuple[int, int]]] = []
    taken: list[tuple[int, int]] = []
    for det in DETECTORS:
        for m in det.pattern.finditer(text):
            span = m.span(1) if m.groups() and m.group(1) else m.span()
            value = text[span[0]:span[1]].strip()
            if det.check and not det.check(value):  # type: ignore[operator]
                continue
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            taken.append(span)
            hits.append((det, value, span))
    return hits


def scan(memory: MemoryDoc, chats: ChatArchive | None = None) -> list[Finding]:
    found: dict[tuple[str, str], Finding] = {}

    def record(text: str, where: str) -> None:
        for det, value, _ in scan_text(text):
            key = (det.kind, value.lower())
            f = found.setdefault(key, Finding(det.kind, det.label, value))
            if where not in f.locations:
                f.locations.append(where)

    for _, fact in memory.iter_facts():
        record(fact.text, fact.id)
    if chats:
        for conv in chats.conversations:
            for msg in conv.messages:
                record(msg.text, f"chat:{conv.id}")
    return sorted(found.values(), key=lambda f: (f.kind, f.value.lower()))


def _redact(text: str, findings: list[Finding]) -> str:
    for f in findings:
        text = re.sub(re.escape(f.value), f"[removed {f.label.lower()}]", text, flags=re.I)
    return text


def apply_redactions(memory: MemoryDoc, chats: ChatArchive | None, remove: list[Finding]) -> tuple[MemoryDoc, ChatArchive | None]:
    """Return copies of memory/chats with the chosen identifiers replaced by ``[removed …]``."""
    if not remove:
        return memory, chats
    from .schema import Conversation, Fact, Message  # local to avoid cycles in type checkers

    new_mem = MemoryDoc(memory.source_provider, generated_at=memory.generated_at)
    for cat, fact in memory.iter_facts():
        text = _redact(fact.text, remove)
        if len(re.sub(r"\[removed [^\]]+\]|\W", "", text)) < 3:
            continue  # nothing meaningful left
        new_mem.add(cat, Fact(text=text, source_provider=fact.source_provider, date=fact.date,
                              origin=fact.origin, id=fact.id if text == fact.text else ""))
    new_chats = None
    if chats is not None:
        new_chats = ChatArchive(chats.source_provider)
        for conv in chats.conversations:
            new_chats.conversations.append(Conversation(
                id=conv.id, title=_redact(conv.title, remove), source_provider=conv.source_provider,
                created=conv.created, updated=conv.updated,
                messages=[Message(m.role, _redact(m.text, remove), m.time) for m in conv.messages]))
    return new_mem, new_chats


def parse_selection(answer: str, count: int) -> list[int]:
    """Parse '1,3-5', 'all', 'none' into zero-based indexes."""
    answer = answer.strip().lower()
    if answer in ("", "none", "n", "no", "0"):
        return []
    if answer in ("all", "a", "*", "y", "yes"):
        return list(range(count))
    chosen: set[int] = set()
    for part in re.split(r"[,\s]+", answer):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            chosen.update(range(lo, hi + 1))
        elif part.isdigit():
            chosen.add(int(part))
        else:
            raise ValueError(f"not a number or range: {part!r}")
    bad = [i for i in chosen if not 1 <= i <= count]
    if bad:
        raise ValueError(f"out of range: {bad} (valid 1-{count})")
    return sorted(i - 1 for i in chosen)


def select_by_policy(findings: list[Finding], policy: str) -> list[Finding]:
    """Non-interactive selection: 'all', 'none', or a comma list of kinds (e.g. 'email,phone')."""
    policy = policy.strip().lower()
    if policy == "all":
        return list(findings)
    if policy in ("", "none"):
        return []
    kinds = {k.strip() for k in policy.split(",")}
    return [f for f in findings if f.kind in kinds]


def review_markdown(findings: list[Finding], removed: list[Finding]) -> str:
    removed_keys = {(f.kind, f.value) for f in removed}
    lines = ["# Privacy review", "", "Local file only. Nothing in this list is sent anywhere.", "",
             "| # | Type | Value | Found in | Action |", "|---|------|-------|----------|--------|"]
    for i, f in enumerate(findings, 1):
        where = ", ".join(f.locations[:5]) + (" …" if len(f.locations) > 5 else "")
        action = "removed" if (f.kind, f.value) in removed_keys else "kept"
        lines.append(f"| {i} | {f.label} | `{f.value}` | {where} | {action} |")
    if not findings:
        lines.append("| – | – | none found | – | – |")
    return "\n".join(lines) + "\n"
