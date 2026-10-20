"""Errors that carry the failing step and a concrete hint for the user."""
from __future__ import annotations


class AiSwitchError(Exception):
    """Base error. ``step`` names the stage that failed, ``hint`` says what to do next."""

    def __init__(self, message: str, *, step: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.step = step
        self.hint = hint

    def render(self) -> str:
        lines = [f"✗ {self.step + ' failed: ' if self.step else ''}{self}"]
        if self.hint:
            lines.append(f"  What you can do: {self.hint}")
        return "\n".join(lines)


class StepError(AiSwitchError):
    """A whole step of the flow failed (every fallback method was exhausted)."""


class MethodUnavailable(AiSwitchError):
    """A fallback method does not apply here (e.g. no export file, Playwright missing)."""


class MethodFailed(AiSwitchError):
    """A fallback method was tried and did not work (selector missing, timeout, parse error)."""


class NotLoggedIn(MethodFailed):
    """The persistent browser profile is not signed in to the provider."""


class UserAborted(AiSwitchError):
    """The user cancelled at an interactive prompt."""
