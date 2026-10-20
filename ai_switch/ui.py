"""Terminal interaction, kept behind a small interface so tests and the web GUI can drive it.

Rule: nothing passed to these methods may contain memory or chat content.
Messages are about steps, counts, file paths and masked identifiers only.
"""
from __future__ import annotations

import sys
from typing import Callable


class Console:
    def __init__(self, interactive: bool | None = None, out=None) -> None:
        self.out = out or sys.stdout
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    def info(self, msg: str) -> None:
        print(msg, file=self.out, flush=True)

    def step(self, n: int, total: int, title: str) -> None:
        self.info(f"\n[{n}/{total}] {title}")

    def ok(self, msg: str) -> None:
        self.info(f"  ✓ {msg}")

    def warn(self, msg: str) -> None:
        self.info(f"  ! {msg}")

    def ask(self, question: str, default: str = "") -> str:
        if not self.interactive:
            return default
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"  ? {question}{suffix}: ").strip()
        except EOFError:
            return default
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        d = "Y/n" if default else "y/N"
        answer = self.ask(f"{question} ({d})", "").lower()
        if not answer:
            return default
        return answer.startswith("y")

    def pause(self, msg: str) -> None:
        """Wait for the user to finish a manual step in their browser."""
        self.info(f"  → {msg}")
        if self.interactive:
            try:
                input("    Press Enter when done… ")
            except EOFError:
                pass

    def read_block(self, prompt: str, terminator: str = "END-MEMORY") -> str:
        """Read multi-line text typed/pasted in the terminal (input is not echoed back by us)."""
        if not self.interactive:
            return ""
        self.info(f"  → {prompt}\n    Finish with a line containing only {terminator} (or Ctrl-D).")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == terminator:
                break
            lines.append(line)
        return "\n".join(lines)


class ScriptedConsole(Console):
    """Console with pre-recorded answers; used by tests and the web GUI."""

    def __init__(self, answers: dict[str, str] | Callable[[str], str] | None = None,
                 blocks: list[str] | None = None) -> None:
        import io

        super().__init__(interactive=True, out=io.StringIO())
        self.answers = answers or {}
        self.blocks = list(blocks or [])
        self.pauses: list[str] = []

    def ask(self, question: str, default: str = "") -> str:
        self.info(f"  ? {question}")
        if callable(self.answers):
            return self.answers(question) or default
        for key, value in self.answers.items():
            if key.lower() in question.lower():
                return value
        return default

    def pause(self, msg: str) -> None:
        self.info(f"  → {msg}")
        self.pauses.append(msg)

    def read_block(self, prompt: str, terminator: str = "END-MEMORY") -> str:
        self.info(f"  → {prompt}")
        return self.blocks.pop(0) if self.blocks else ""

    @property
    def transcript(self) -> str:
        return self.out.getvalue()
