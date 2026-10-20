"""Compare the target AI's answer to "What do you know about me?" with memory.json."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import Fact, MemoryDoc

_STOP = set("""a an the and or but if of to in on at for with by from as is are was were be been being am i me my
mine you your user user's users he she they them their it its this that these those there here has have had do does
did not no yes very really just also about into over than then so such can could would should will shall may might
must like likes prefers prefer s t""".split())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9'+-]*", text.lower())
    return {_stem(w.strip("'")) for w in words if w not in _STOP and len(w) > 1}


def _key_terms(text: str) -> set[str]:
    """Names and numbers: if any of these are missing, the fact is very likely missing."""
    terms = set(re.findall(r"\b\d[\d.,:/-]*\b", text))
    for w in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z]{2,}\b", text):
        if w.lower() not in _STOP:
            terms.add(w.lower())
    return terms


@dataclass
class VerifyReport:
    covered: list[Fact] = field(default_factory=list)
    missing: list[Fact] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    skipped: bool = False

    @property
    def total(self) -> int:
        return len(self.covered) + len(self.missing)

    @property
    def coverage(self) -> float:
        return len(self.covered) / self.total if self.total else 1.0

    def summary(self) -> str:
        if self.skipped:
            return "verification skipped"
        return f"{len(self.covered)}/{self.total} facts recalled ({self.coverage:.0%}); {len(self.missing)} missing"

    def markdown(self) -> str:
        lines = ["# Verification report", "", self.summary(), ""]
        if self.missing:
            lines += ["## Missing", ""] + [f"- `{f.id}` {f.text} (match {self.scores[f.id]:.0%})" for f in self.missing]
            lines.append("")
        if self.covered:
            lines += ["## Recalled", ""] + [f"- `{f.id}` {f.text}" for f in self.covered]
        return "\n".join(lines) + "\n"


def compare(memory: MemoryDoc, answer: str, threshold: float = 0.5) -> VerifyReport:
    answer_kw = keywords(answer)
    answer_lower = answer.lower()
    report = VerifyReport()
    for fact in memory.all_facts():
        kw = keywords(fact.text)
        score = len(kw & answer_kw) / len(kw) if kw else 1.0
        key_terms = _key_terms(fact.text)
        if key_terms and not any(t in answer_lower for t in key_terms):
            score = min(score, threshold - 0.01)
        report.scores[fact.id] = score
        (report.covered if score >= threshold else report.missing).append(fact)
    return report
