"""Turn errors into plain-English messages; technical detail goes to a collapsible section."""
from __future__ import annotations

import traceback

from ..errors import AiSwitchError, MethodFailed, MethodUnavailable, NotLoggedIn, UserAborted


def friendly(exc: BaseException, provider: str = "", step: str = "") -> dict[str, str]:
    name = provider or "The website"
    text = str(exc)
    low = text.lower()
    details = exc.render() if isinstance(exc, AiSwitchError) else f"{type(exc).__name__}: {text}"
    details += "\n\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
    action = ""
    if isinstance(exc, UserAborted):
        title, message = "Stopped", "You stopped the move. Nothing else was sent."
    elif isinstance(exc, NotLoggedIn) or "not signed in" in low or "signed out" in low:
        title, message, action = (f"You're signed out of {name}", "Sign in, then press Try again.", "signin")
    elif "no edge or chrome" in low:
        title = "AI Switch needs a web browser"
        message = "Microsoft Edge or Google Chrome wasn't found. AI Switch can download Chromium for you."
        action = "download_browser"
    elif "verify you are human" in low or "human check" in low:
        title, message = f"{name} wants a quick human check", "Complete it in the browser window, then press Try again."
    elif "could not find" in low or "selector" in low or "layout probably changed" in low:
        title = f"{name} changed its page"
        message = "Click 'Update fixes', then try again. If it still fails, the manual paste option will kick in."
        action = "update_fixes"
    elif "could not open" in low or "connection" in low or "timed out" in low or "no reply" in low:
        title, message = f"Couldn't reach {name}", "Check your internet connection, then press Try again."
    elif "no facts" in low or "listed no memories" in low or "contains no memory" in low:
        title = f"{name} didn't share any memories"
        message = ("It may have nothing saved yet, or its memory is turned off. You can also pick "
                   "'Use export file' on the Home screen for a more complete copy.")
    elif isinstance(exc, (MethodFailed, MethodUnavailable)) or isinstance(exc, AiSwitchError):
        title = f"Something went wrong{' while ' + step.lower() if step else ''}"
        hint = getattr(exc, "hint", "")
        plain = text.split(" — ")[0].strip().rstrip(".")
        message = hint or (plain[:1].upper() + plain[1:] + "." if plain and len(plain) < 120 else
                           "Press Try again. If it keeps happening, open Details and send them to the developer.")
    else:
        title = f"Something unexpected happened{' while ' + step.lower() if step else ''}"
        message = "Press Try again. Details are in the log file in the output folder."
    return {"title": title, "message": message, "details": details, "action": action}
