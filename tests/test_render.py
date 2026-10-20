import pytest

from ai_switch.render import build_import_parts, chunk_text, memory_markdown, render_block
from ai_switch.schema import Fact, MemoryDoc


def big_doc(n=120) -> MemoryDoc:
    doc = MemoryDoc("chatgpt")
    for i in range(n):
        doc.add(["profile", "preferences", "people", "projects", "topics"][i % 5],
                Fact(f"Fact number {i} about the user with some extra words to take space", "chatgpt"))
    return doc


@pytest.mark.parametrize("style,limit", [("block", 1500), ("remember", 1000), ("profile", 1500),
                                         ("primer", 2000), ("lines_block", 800)])
def test_parts_fit_limit_and_keep_every_fact(style, limit):
    doc = big_doc()
    parts = build_import_parts(doc, style, limit)
    assert len(parts) > 1
    assert all(len(p) <= limit for p in parts)
    joined = "\n".join(parts)
    for f in doc.all_facts():
        assert f.text in joined


def test_lines_style_one_fact_per_part_with_item_limit():
    doc = big_doc(3)
    parts = build_import_parts(doc, "lines", 1000, item_limit=30)
    assert len(parts) == 3 and all(len(p) <= 30 for p in parts)


def test_remember_prefix_on_every_part():
    parts = build_import_parts(big_doc(), "remember", 900)
    assert parts[0].startswith("Please remember")
    assert all(p.startswith(("Please remember", "Please also remember")) for p in parts)


def test_chunk_hard_splits_overlong_line():
    parts = chunk_text("word " * 500, 120)
    assert all(len(p) <= 120 for p in parts)
    assert "".join(parts).count("word") == 500


def test_markdown_outputs():
    doc = big_doc(5)
    md = memory_markdown(doc)
    assert md.count("## ") == 5 and "<!-- f-" in md
    assert render_block(doc).startswith("Here is what my previous assistant knew about me")
