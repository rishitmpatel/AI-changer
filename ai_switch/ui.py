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
        # windowed apps (AI Switch.exe) have no stdin/stdout at all
        has_tty = bool(sys.stdin is not None and hasattr(sys.stdin, "isatty") and sys.stdin.isatty())
        self.interactive = has_tty if interactive is None else interactive

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

    def wait_for(self, card: dict, check=None, poll: float = 2.0, timeout: float | None = None) -> bool:
        """Show a "do this in the browser" card and return once ``check()`` is true (auto-detected)
        or the user says it's done. Returns True when the check detected it."""
        import threading
        import time

        self.info(f"  → {card.get('title', '')}: {card.get('text', '')}")
        if card.get("steps"):
            self.info(f"    ({card['steps']})")
        if check is None or not self.interactive:
            self.pause("Do it in the browser window")
            return False
        done = threading.Event()

        def _enter() -> None:
            try:
                input("    I'll notice automatically — or press Enter when done… ")
            except EOFError:
                pass
            done.set()

        threading.Thread(target=_enter, daemon=True).start()
        start = time.monotonic()
        while not done.is_set():
            if check():
                self.ok("detected — continuing")
                return True
            if timeout is not None and time.monotonic() - start > timeout:
                return False
            done.wait(poll)
        return False

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

    def wait_for(self, card: dict, check=None, poll: float = 2.0, timeout: float | None = None) -> bool:
        self.info(f"  → {card.get('title', '')}: {card.get('text', '')}")
        self.cards = getattr(self, "cards", []) + [card]
        self.pauses.append(" ".join(filter(None, [card.get("text", ""), card.get("steps", "")])))
        return bool(check and check())

    @property
    def transcript(self) -> str:
        return self.out.getvalue()
