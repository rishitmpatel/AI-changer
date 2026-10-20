"""Cross-platform clipboard with no network and graceful fallbacks."""
from __future__ import annotations

import shutil
import subprocess
import sys

from .errors import MethodUnavailable


class Clipboard:
    """Copy/paste text. Tries pyperclip, then the OS tools (pbcopy, clip/PowerShell, wl-copy, xclip, xsel)."""

    def copy(self, text: str) -> None:
        try:
            import pyperclip  # type: ignore

            pyperclip.copy(text)
            return
        except Exception:
            pass
        for cmd in self._copy_cmds():
            if shutil.which(cmd[0]):
                subprocess.run(cmd, input=text.encode("utf-8"), check=True)
                return
        raise MethodUnavailable("no clipboard tool found",
                                hint="pip install pyperclip, or on Linux install xclip / wl-clipboard.")

    def paste(self) -> str:
        try:
            import pyperclip  # type: ignore

            return pyperclip.paste()
        except Exception:
            pass
        for cmd in self._paste_cmds():
            if shutil.which(cmd[0]):
                res = subprocess.run(cmd, capture_output=True, check=True)
                return res.stdout.decode("utf-8", errors="replace")
        raise MethodUnavailable("no clipboard tool found",
                                hint="pip install pyperclip, or on Linux install xclip / wl-clipboard.")

    @staticmethod
    def _copy_cmds() -> list[list[str]]:
        if sys.platform == "darwin":
            return [["pbcopy"]]
        if sys.platform.startswith("win"):
            return [["clip"]]
        return [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]

    @staticmethod
    def _paste_cmds() -> list[list[str]]:
        if sys.platform == "darwin":
            return [["pbpaste"]]
        if sys.platform.startswith("win"):
            return [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
        return [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"]]


class MemoryClipboard(Clipboard):
    """In-memory clipboard for tests and headless runs."""

    def __init__(self) -> None:
        self.value = ""
        self.history: list[str] = []

    def copy(self, text: str) -> None:
        self.value = text
        self.history.append(text)

    def paste(self) -> str:
        return self.value
