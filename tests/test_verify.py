from ai_switch.extract import parse_memory_text
from ai_switch.verify import compare, keywords


def test_compare_against_real_looking_answer(fixtures_dir):
    memory = parse_memory_text((fixtures_dir / "replies" / "chatgpt_memory_reply.txt").read_text(), "chatgpt", "ask_ai")
    answer = (fixtures_dir / "replies" / "claude_verify_answer.txt").read_text()
    report = compare(memory, answer)
    missing = {f.text for f in report.missing}
    assert "Partner is named Sam; they have a dog called Miso." in missing
    assert any("alex.rivera" in t for t in missing)
    covered = {f.text for f in report.covered}
    assert "Lives in Austin, Texas." in covered
    assert "Prefers concise answers with code first." in covered
    assert 0 < report.coverage < 1
    assert "missing" in report.summary()
    assert "## Missing" in report.markdown()


def test_names_must_appear():
    memory = parse_memory_text("- Partner is named Sam", "x", "t")
    assert compare(memory, "You have a partner.").missing
    assert not compare(memory, "Your partner is Sam.").missing


def test_keywords_stems():
    assert keywords("I like bouldering") == {"boulder"}
