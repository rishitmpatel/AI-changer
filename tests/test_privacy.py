import pytest

from ai_switch import privacy
from ai_switch.schema import ChatArchive, Conversation, Fact, MemoryDoc, Message


def kinds(text):
    return sorted({d.kind for d, _, _ in privacy.scan_text(text)})


@pytest.mark.parametrize("text,kind", [
    ("mail me at alex.rivera@example.com", "email"),
    ("call +1 415 555 0134 after 5", "phone"),
    ("my number is (020) 7946 0958", "phone"),
    ("+91 98765 43210", "phone"),
    ("I live at 742 Evergreen Terrace, Springfield", "address"),
    ("office: 221B Baker Street, London NW1 6XE", "address"),
    ("SSN 123-45-6789", "ssn"),
    ("card 4111 1111 1111 1111 exp 12/29", "card"),
    ("IBAN GB82 WEST 1234 5698 7654 32", "iban"),
    ("Aadhaar 2345 6789 0124", "aadhaar"),
    ("PAN is ABCDE1234F", "pan"),
    ("passport number X12345678", "id_number"),
    ("born on 12 March 1990", "dob"),
    ("server 192.168.10.24", "ip"),
])
def test_detects(text, kind):
    assert kind in kinds(text)


@pytest.mark.parametrize("text", [
    "Saved on 2026-05-01 at 10:30", "Released tidyfs 1.0 in 2026", "Budget is $15k for 3 rooms",
    "card 4111 1111 1111 1112",  # fails Luhn
    "Prefers concise answers with code first.",
])
def test_no_false_positives(text):
    assert kinds(text) == []


def test_mask_never_reveals_full_value():
    assert privacy.mask("alex.rivera@example.com", "email") == "a***@e***.com"
    masked = privacy.mask("+1 415 555 0134", "phone")
    assert masked.startswith("+1 ") and "555" not in masked and masked.endswith("134")


def test_scan_dedupes_and_tracks_locations():
    doc = MemoryDoc("x")
    doc.add("profile", Fact("Email is a@b.io", "x"))
    doc.add("topics", Fact("Also reachable at A@B.IO for work", "x"))
    chats = ChatArchive("x", [Conversation("c9", "t", "x", [Message("user", "ping a@b.io")])])
    found = privacy.scan(doc, chats)
    assert len(found) == 1 and len(found[0].locations) == 3 and "chat:c9" in found[0].locations


def test_redaction_and_drop_empty():
    doc = MemoryDoc("x")
    doc.add("profile", Fact("Phone number is +1 415 555 0134 for SMS reminders.", "x"))
    doc.add("profile", Fact("+44 20 7946 0958", "x"))
    found = privacy.scan(doc)
    new, _ = privacy.apply_redactions(doc, None, found)
    assert [f.text for f in new.all_facts()] == ["Phone number is [removed phone number] for SMS reminders."]


def test_parse_selection():
    assert privacy.parse_selection("1,3-4", 5) == [0, 2, 3]
    assert privacy.parse_selection("all", 3) == [0, 1, 2]
    assert privacy.parse_selection("none", 3) == []
    with pytest.raises(ValueError):
        privacy.parse_selection("7", 3)
    with pytest.raises(ValueError):
        privacy.parse_selection("x", 3)


def test_policy_and_review_markdown():
    doc = MemoryDoc("x")
    doc.add("profile", Fact("Email a@b.io, phone +1 415 555 0134", "x"))
    found = privacy.scan(doc)
    chosen = privacy.select_by_policy(found, "email")
    assert [f.kind for f in chosen] == ["email"]
    md = privacy.review_markdown(found, chosen)
    assert "| removed |" in md and "| kept |" in md
