"""A move running in the background, reporting plain-English progress to the UI.

The job only stops for the user in four situations, each shown as a "card":
``signin`` (an account isn't signed in), ``privacy`` (the review step), ``challenge``
(a CAPTCHA / 2FA in the browser) and ``paste`` / ``manual`` (last-resort manual fallback).
Every card that can be auto-detected is: the job keeps polling and continues by itself.
"""
from __future__ import annotations

import dataclasses
import itertools
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import migrate, privacy
from ..adapters import Context, get_adapter, resolve
from ..clipboard import Clipboard
from ..errors import AiSwitchError, StepError, UserAborted
from ..extract import VERIFY_PROMPT
from ..render import memory_markdown
from ..schema import CATEGORIES, ChatArchive, Fact, MemoryDoc, write_private
from ..ui import Console
from ..verify import VerifyReport, compare
from .friendly import friendly

log = logging.getLogger("ai_switch.desktop")
STEPS = [("export", "Export"), ("review", "Review"), ("import", "Import"), ("verify", "Verify")]
_card_ids = itertools.count(1)


class _Sink:
    def __init__(self, job: "Job") -> None:
        self.job = job

    def write(self, s: str) -> None:
        for line in s.splitlines():
            if line.strip():
                self.job.details.append(line.rstrip())

    def flush(self) -> None:
        pass


class AppConsole(Console):
    """Console used by the engine inside the app: technical lines go to Details, prompts become cards."""

    def __init__(self, job: "Job") -> None:
        super().__init__(interactive=True, out=_Sink(job))
        self.job = job

    def warn(self, msg: str) -> None:
        super().warn(msg)
        self.job.say("That didn't work — trying another way…", dedupe=True)

    def ask(self, question: str, default: str = "") -> str:
        return default

    def confirm(self, question: str, default: bool = True) -> bool:
        return default

    def pause(self, msg: str) -> None:
        self.job.wait_card({"kind": "manual", "title": "Your turn", "text": msg})

    def read_block(self, prompt: str, terminator: str = "END-MEMORY") -> str:
        return str(self.job.wait_card({"kind": "paste_reply", "title": "Paste the reply", "text": prompt}) or "")

    def wait_for(self, card: dict, check: Callable[[], bool] | None = None, poll: float = 2.0,
                 timeout: float | None = None) -> bool:
        return self.job.wait_card(card, check, poll=poll) == "__detected__"


class Job:
    """Base: status, cards, cancellation. Subclasses implement :meth:`work`."""

    kind = "job"

    def __init__(self, services: Any) -> None:
        self.services = services
        self.lines: list[str] = []
        self.details: list[str] = []
        self.steps = [{"id": i, "title": t, "status": "pending"} for i, t in STEPS]
        self.progress = 0.0
        self.card: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.error: dict[str, str] | None = None
        self.done = False
        self.cancelled = threading.Event()
        self._answer: Any = None
        self._answered = threading.Event()
        self.thread: threading.Thread | None = None
        self.started = time.time()

    # ------------------------------------------------------------------ status for the UI
    def status(self) -> dict[str, Any]:
        return {"kind": self.kind, "lines": self.lines[-60:], "steps": self.steps, "progress": round(self.progress),
                "card": self.card, "result": self.result, "error": self.error, "done": self.done}

    def say(self, line: str, dedupe: bool = False) -> None:
        if dedupe and self.lines and self.lines[-1] == line:
            return
        self.lines.append(line)
        log.info("ui: %s", line)

    def set_step(self, step_id: str, status: str, progress: float | None = None) -> None:
        for s in self.steps:
            if s["id"] == step_id:
                s["status"] = status
        if progress is not None:
            self.progress = progress

    # ------------------------------------------------------------------ cards
    def wait_card(self, card: dict[str, Any], check: Callable[[], bool] | None = None, poll: float = 1.5) -> Any:
        card = {**card, "id": next(_card_ids), "auto": check is not None}
        self._answered.clear()
        self._answer = None
        self.card = card
        try:
            while True:
                if self.cancelled.is_set():
                    raise UserAborted("cancelled", step="")
                if check is not None:
                    try:
                        if check():
                            return "__detected__"
                    except Exception:
                        pass
                if self._answered.wait(poll):
                    if self.cancelled.is_set():
                        raise UserAborted("cancelled", step="")
                    return self._answer
        finally:
            self.card = None

    def answer(self, card_id: int, value: Any) -> bool:
        if not self.card or self.card.get("id") != card_id:
            return False
        self._answer = value
        self._answered.set()
        return True

    def cancel(self) -> None:
        self.cancelled.set()
        self._answered.set()

    # ------------------------------------------------------------------ running
    def start(self) -> "Job":
        self.thread = threading.Thread(target=self._main, daemon=True, name=f"ai-switch-{self.kind}")
        self.thread.start()
        return self

    def _main(self) -> None:
        try:
            self.work()
        except BaseException as exc:  # noqa: BLE001 - everything becomes a friendly card
            current = next((s for s in self.steps if s["status"] == "active"), None)
            if current:
                current["status"] = "error"
            self.error = friendly(exc, getattr(self, "error_provider", ""), current["title"] if current else "")
            log.exception("job failed")
        finally:
            self.done = True
            try:
                self.on_finished()
            except Exception:
                log.exception("on_finished failed")

    def work(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def on_finished(self) -> None:
        pass


class MoveJob(Job):
    kind = "move"

    def __init__(self, services: Any, source: str, target: str, *, chats: bool = False,
                 export_path: str | None = None, only_facts: MemoryDoc | None = None) -> None:
        super().__init__(services)
        self.source, self.target = resolve(source), resolve(target)
        if self.source == self.target:
            raise StepError("source and target are the same AI", step="Choosing providers")
        self.chats = chats
        self.export_path = Path(export_path) if export_path else None
        self.only_facts = only_facts  # "Retry missing": skip export/review, import just these
        self.src_name = get_adapter(self.source).name
        self.dst_name = get_adapter(self.target).name
        self.error_provider = self.src_name
        self.out_dir: Path | None = None
        self.memory: MemoryDoc | None = None
        self.report: VerifyReport | None = None

    # ------------------------------------------------------------------ helpers
    def ctx(self) -> Context:
        svc = self.services
        return Context(console=AppConsole(self), clipboard=getattr(svc, "clipboard", None) or Clipboard(),
                       browser_factory=getattr(svc, "browser_factory", None), browser_manager=svc.manager,
                       export_path=self.export_path, out_dir=Path(svc.settings.output_dir), auto_merge=True,
                       on_challenge=self._on_challenge, strict_network=True,
                       offline=getattr(svc, "offline", False))

    def _on_challenge(self, session: Any) -> None:
        mgr = self.services.manager
        was_visible = getattr(mgr, "visible", False)
        if mgr is not None:
            mgr.set_visible(True)
        name = session.config.display_name
        self.say(f"{name} wants to check you're human — waiting for you in the browser window…")
        self.wait_card({"kind": "challenge", "title": "Quick check needed",
                        "text": f"{name} is showing a 'verify you are human' check or a sign-in code. Complete it in "
                                f"the browser window — I'll continue automatically."},
                       check=lambda: not session.challenge_present())
        if mgr is not None and not was_visible:
            mgr.set_visible(False)

    def ensure_signed_in(self, provider: str) -> None:
        accounts = self.services.accounts
        if accounts is None:
            return
        state = accounts.status(provider) if hasattr(accounts, "status") else "unknown"
        if state != "signed_out":
            return
        name = get_adapter(provider).name
        self.say(f"You need to sign in to {name} first.")
        self.wait_card({"kind": "signin", "provider": provider, "title": f"Sign in to {name}",
                        "text": f"Click Sign in and log in to {name} in the browser window that opens "
                                f"(passwords, Google sign-in and 2FA all work normally). I'll notice when you're "
                                f"signed in — or just close that window when you're done."},
                       check=lambda: accounts.cache.get(provider) == "signed_in")
        self.say(f"Signed in to {name} ✓")

    # ------------------------------------------------------------------ the move
    def work(self) -> None:
        ctx = self.ctx()
        self.out_dir = migrate.make_out_dir(ctx.out_dir, f"{self.source}-to-{self.target}")
        handler = migrate.attach_run_log(self.out_dir)
        ctx = dataclasses.replace(ctx, out_dir=self.out_dir)
        try:
            if self.only_facts is not None:
                self._retry_only(ctx)
            else:
                self._full(ctx)
        finally:
            logging.getLogger("ai_switch").removeHandler(handler)
            handler.close()

    def _full(self, ctx: Context) -> None:
        # 1. export
        self.set_step("export", "active", 3)
        if not self.export_path:
            self.ensure_signed_in(self.source)
        self.ensure_signed_in(self.target)
        self.say(f"Reading what {self.src_name} remembers about you…")
        memory, chats, src = migrate.export_step(ctx, self.source, self.chats, self.out_dir)
        how = " + ".join(m.label.split(" ", 1)[1] for m in (src.merged_methods or [src.last_method]) if m)
        self.say(f"Found {len(memory)} facts ({how}).")
        if chats is not None:
            self.say(f"Found {len(chats)} conversations.")
        self.set_step("export", "done", 30)
        # 2. privacy review (always shown: it's the user's last look before anything is sent)
        self.set_step("review", "active", 32)
        memory, chats = self._review(memory, chats)
        self.memory = memory
        self.set_step("review", "done", 45)
        # 3. import
        self.error_provider = self.dst_name
        self.set_step("import", "active", 48)
        self.say(f"Adding your memory to {self.dst_name}…")
        imported, chat_res, tgt = migrate.import_step(ctx, self.target, memory, chats, self.out_dir)
        self.say(f"Sent {imported.parts} part(s) to {self.dst_name}" + (" (pasted by you)." if imported.manual else "."))
        if chat_res is not None:
            self.say("Chat history " + ("is ready for you to attach." if chat_res.manual else "uploaded."))
        self.set_step("import", "done", 75)
        # 4. verify (+ automatic retry of whatever is missing)
        self._verify(ctx, tgt, memory)

    def _review(self, memory: MemoryDoc, chats: ChatArchive | None) -> tuple[MemoryDoc, ChatArchive | None]:
        findings = privacy.scan(memory, chats)
        self.say(f"Found {len(findings)} personal detail(s) to review." if findings else "No phone numbers, emails "
                 "or addresses found. Check the preview, then continue.")
        card = {"kind": "privacy", "title": "Check before sending",
                "findings": [{"index": i, "kind": f.kind, "label": f.label, "masked": f.masked, "value": f.value,
                              "places": len(f.locations)} for i, f in enumerate(findings)],
                "facts": [{"id": f.id, "category": c, "text": f.text} for c, f in memory.iter_facts()],
                "categories": list(CATEGORIES)}
        decision = self.wait_card(card) or {}
        memory = apply_edits(memory, decision.get("facts"))
        remove = [findings[i] for i in decision.get("remove", []) if 0 <= int(i) < len(findings)]
        memory, chats = privacy.apply_redactions(memory, chats, remove)
        write_private(self.out_dir / "privacy_review.md", privacy.review_markdown(findings, remove))
        memory.save(self.out_dir / "memory.json")
        write_private(self.out_dir / "memory.md", memory_markdown(memory))
        if chats is not None:
            chats.save(self.out_dir / "chats.json")
        self.say(f"Removed {len(remove)} detail(s). Sending {len(memory)} facts." if remove
                 else f"Sending {len(memory)} facts.")
        if not len(memory):
            raise StepError("there are no facts left to send", step="Review",
                            hint="You removed every fact. Go back and keep at least one.")
        return memory, chats

    def _verify(self, ctx: Context, tgt: Any, memory: MemoryDoc, retry: bool = True) -> None:
        self.set_step("verify", "active", 80)
        self.say(f"Asking {self.dst_name} what it knows about you…")
        report = migrate.verify_step(ctx, tgt, memory, self.out_dir, retry=False)
        if report.missing and not report.skipped and retry:
            self.say(f"{len(report.missing)} fact(s) missing — sending them again…")
            tgt.import_memory(memory.subset({f.id for f in report.missing}))
            self.progress = 90
            answer = tgt.ask(VERIFY_PROMPT)
            write_private(self.out_dir / "verify_answer_2.txt", answer)
            report = compare(memory, answer)
            write_private(self.out_dir / "verify_report.md", report.markdown())
        self.report = report
        self.set_step("verify", "skipped" if report.skipped else "done", 100)
        self.result = {"moved": len(report.covered), "total": report.total, "skipped": report.skipped,
                       "missing": [{"id": f.id, "text": f.text} for f in report.missing],
                       "out_dir": str(self.out_dir), "source": self.source, "target": self.target,
                       "note": ("Claude can take up to 24 hours to absorb imported memory — check again later."
                                if self.target == "claude" and report.missing else "")}
        self.say(f"Done — {self.dst_name} recalled {len(report.covered)} of {report.total} facts."
                 if not report.skipped else "Done — verification was skipped.")

    def _retry_only(self, ctx: Context) -> None:
        for s in self.steps[:2]:
            s["status"] = "skipped"
        self.error_provider = self.dst_name
        self.ensure_signed_in(self.target)
        self.set_step("import", "active", 40)
        self.say(f"Sending {len(self.only_facts)} missing fact(s) to {self.dst_name} again…")
        tgt = get_adapter(self.target, ctx)
        tgt.import_memory(self.only_facts)
        self.set_step("import", "done", 75)
        self._verify(ctx, tgt, self.only_facts, retry=False)

    def on_finished(self) -> None:
        history = getattr(self.services, "history", None)
        if history is None:
            return
        entry = {"source": self.source, "target": self.target, "out_dir": str(self.out_dir or ""),
                 "kind": "retry" if self.only_facts is not None else "move"}
        if self.result:
            entry.update(status="ok" if not self.result["missing"] else "partial",
                         moved=self.result["moved"], total=self.result["total"])
        else:
            entry.update(status="cancelled" if self.cancelled.is_set() else "failed",
                         error=(self.error or {}).get("title", ""))
        history.add(entry)


def apply_edits(memory: MemoryDoc, facts: list[dict[str, Any]] | None) -> MemoryDoc:
    """Apply the user's edits from the review screen (changed text, deleted facts, moved category)."""
    if facts is None:
        return memory
    by_id = {f.id: (c, f) for c, f in memory.iter_facts()}
    out = MemoryDoc(memory.source_provider, generated_at=memory.generated_at)
    for item in facts:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        cat = item.get("category") if item.get("category") in CATEGORIES else None
        orig = by_id.get(str(item.get("id", "")))
        if orig:
            ocat, of = orig
            new = Fact(text=text, source_provider=of.source_provider, date=of.date, origin=of.origin,
                       id=of.id if text == of.text else "")
            out.add(cat or ocat, new)
        else:  # a fact the user added
            out.add(cat or "topics", Fact(text=text, source_provider=memory.source_provider, origin="edited"))
    return out
