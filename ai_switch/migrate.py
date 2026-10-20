"""The move / export / import / verify flows used by the CLI and the web GUI."""
from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import privacy
from .adapters import BaseAdapter, Context, ImportResult, get_adapter, resolve
from .errors import AiSwitchError, StepError, UserAborted
from .extract import VERIFY_PROMPT
from .render import chats_markdown, memory_markdown
from .schema import ChatArchive, MemoryDoc, write_private
from .verify import VerifyReport, compare

log = logging.getLogger("ai_switch")


@dataclass
class RunResult:
    out_dir: Path
    memory: MemoryDoc | None = None
    chats: ChatArchive | None = None
    removed: list[privacy.Finding] = field(default_factory=list)
    imported: ImportResult | None = None
    chats_imported: ImportResult | None = None
    report: VerifyReport | None = None
    export_method: str = ""


def make_out_dir(base: Path, label: str) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(base).expanduser() / f"{stamp}-{label}"
    out.mkdir(parents=True, exist_ok=True)
    try:
        out.chmod(0o700)
    except OSError:  # pragma: no cover
        pass
    return out


def attach_run_log(out_dir: Path) -> logging.Handler:
    """Log step names and counts (never memory content) to <out>/run.log."""
    handler = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("ai_switch")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def _ctx_for(ctx: Context, **changes) -> Context:
    return dataclasses.replace(ctx, **changes)


# --------------------------------------------------------------------------- steps


def export_step(ctx: Context, source: str, with_chats: bool, out_dir: Path) -> tuple[MemoryDoc, ChatArchive | None, BaseAdapter]:
    adapter = get_adapter(source, _ctx_for(ctx, out_dir=out_dir))
    memory = adapter.export_memory()
    method = adapter.last_method.label if adapter.last_method else "?"
    memory.save(out_dir / "memory.json")
    write_private(out_dir / "memory.md", memory_markdown(memory))
    counts = ", ".join(f"{k} {v}" for k, v in memory.counts().items() if v)
    ctx.console.ok(f"{len(memory)} facts via {method} ({counts or 'none'})")
    ctx.console.ok(f"saved {out_dir / 'memory.json'} and memory.md")
    chats = None
    if with_chats:
        chats = adapter.export_chats()
        chats.save(out_dir / "chats.json")
        write_private(out_dir / "chats.md", chats_markdown(chats))
        ctx.console.ok(f"{len(chats)} conversations / {chats.message_count()} messages → chats.json, chats.md")
    return memory, chats, adapter


def privacy_step(ctx: Context, memory: MemoryDoc, chats: ChatArchive | None, out_dir: Path,
                 policy: str | None) -> tuple[MemoryDoc, ChatArchive | None, list[privacy.Finding]]:
    console = ctx.console
    findings = privacy.scan(memory, chats)
    if not findings:
        console.ok("no phone numbers, emails, addresses or ID numbers found")
        write_private(out_dir / "privacy_review.md", privacy.review_markdown([], []))
        return memory, chats, []
    console.info(f"  Found {len(findings)} personal identifier(s). Values are masked here; full list in "
                 f"{out_dir / 'privacy_review.md'}")
    for i, f in enumerate(findings, 1):
        where = f"{len(f.locations)} place(s)"
        console.info(f"   {i:>3}. {f.label:<30} {f.masked:<28} {where}")
    if policy is not None:
        remove = privacy.select_by_policy(findings, policy)
    elif not console.interactive:
        remove = list(findings)
        console.warn("non-interactive run: removing all identifiers (use --redact none to keep them)")
    else:
        write_private(out_dir / "privacy_review.md", privacy.review_markdown(findings, []))
        while True:
            answer = console.ask("Numbers to REMOVE before sending (e.g. 1,3-5 · all · none)", "all")
            try:
                idx = privacy.parse_selection(answer, len(findings))
                break
            except ValueError as exc:
                console.warn(str(exc))
        remove = [findings[i] for i in idx]
    memory, chats = privacy.apply_redactions(memory, chats, remove)
    write_private(out_dir / "privacy_review.md", privacy.review_markdown(findings, remove))
    memory.save(out_dir / "memory.json")
    write_private(out_dir / "memory.md", memory_markdown(memory))
    if chats is not None:
        chats.save(out_dir / "chats.json")
        write_private(out_dir / "chats.md", chats_markdown(chats))
    console.ok(f"removed {len(remove)} of {len(findings)} identifier(s); memory.json updated ({len(memory)} facts)")
    return memory, chats, remove


def import_step(ctx: Context, target: str, memory: MemoryDoc, chats: ChatArchive | None,
                out_dir: Path) -> tuple[ImportResult, ImportResult | None, BaseAdapter]:
    adapter = get_adapter(target, _ctx_for(ctx, out_dir=out_dir, export_path=None))
    result = adapter.import_memory(memory)
    if result.manual:
        ctx.console.ok(f"{result.parts} part(s) prepared for you to paste into {adapter.name} (steps above)")
    else:
        ctx.console.ok(f"memory sent to {adapter.name} via {result.method.label} in {result.parts} part(s)")
    chat_result = None
    if chats is not None and len(chats):
        chat_result = adapter.import_chats(chats)
        if chat_result.manual:
            ctx.console.ok(f"chat files prepared for manual upload to {adapter.name}: {chat_result.note}")
        else:
            ctx.console.ok(f"chat history sent to {adapter.name} via {chat_result.method.label}"
                           + (f" ({chat_result.note})" if chat_result.note else ""))
    return result, chat_result, adapter


def verify_step(ctx: Context, adapter: BaseAdapter, memory: MemoryDoc, out_dir: Path,
                retry: bool | None) -> VerifyReport:
    console = ctx.console
    try:
        answer = adapter.ask(VERIFY_PROMPT)
    except StepError as exc:  # import already happened; don't fail the run, say how to verify later
        exc.hint = (f"Verify later with: ai-switch verify --to {adapter.id} --memory {out_dir / 'memory.json'}"
                    + (f"  ({exc.hint})" if exc.hint and "--export" not in exc.hint else ""))
        console.info("  " + exc.render().replace("\n", "\n  "))
        return VerifyReport(covered=[], missing=[], scores={}, skipped=True)
    write_private(out_dir / "verify_answer.txt", answer)
    report = compare(memory, answer)
    write_private(out_dir / "verify_report.md", report.markdown())
    console.ok(f"{report.summary()} — details in {out_dir / 'verify_report.md'}")
    if not report.missing:
        return report
    if adapter.id == "claude":
        console.info("    note: Claude can take up to 24 h to absorb imported memory; re-run `ai-switch verify` later.")
    do_retry = retry if retry is not None else console.confirm(
        f"Retry importing the {len(report.missing)} missing fact(s)?", default=False)
    if do_retry:
        missing = memory.subset({f.id for f in report.missing})
        adapter.import_memory(missing)
        console.ok(f"re-sent {len(missing)} fact(s); run `ai-switch verify --to {adapter.id} --memory "
                   f"{out_dir / 'memory.json'}` later to check again")
    return report


# --------------------------------------------------------------------------- commands


def _guard(step: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (AiSwitchError, UserAborted):
        raise
    except KeyboardInterrupt:
        raise UserAborted("cancelled", step=step)
    except Exception as exc:  # unexpected bug: still say which step and keep content out of the message
        log.exception("unexpected error in %s", step)
        raise StepError(f"unexpected {type(exc).__name__}", step=step,
                        hint="See run.log in the output folder, and report the error type (not your data).") from exc


def move(ctx: Context, source: str, target: str, *, with_chats: bool = False, redact: str | None = None,
         verify: bool = True, retry: bool | None = None) -> RunResult:
    source, target = resolve(source), resolve(target)
    if source == target:
        raise StepError("source and target are the same provider", step="Choosing providers")
    out = make_out_dir(ctx.out_dir, f"{source}-to-{target}")
    handler = attach_run_log(out)
    res = RunResult(out)
    total = 4 if verify else 3
    try:
        ctx.console.step(1, total, f"Export from {get_adapter(source).name}")
        memory, chats, src = _guard("Step 1 (export)", export_step, ctx, source, with_chats, out)
        res.export_method = src.last_method.value if src.last_method else ""
        ctx.console.step(2, total, "Privacy review")
        memory, chats, res.removed = _guard("Step 2 (privacy review)", privacy_step, ctx, memory, chats, out, redact)
        res.memory, res.chats = memory, chats
        ctx.console.step(3, total, f"Import into {get_adapter(target).name}")
        res.imported, res.chats_imported, tgt = _guard("Step 3 (import)", import_step, ctx, target, memory, chats, out)
        if verify:
            ctx.console.step(4, total, "Verify")
            res.report = _guard("Step 4 (verify)", verify_step, ctx, tgt, memory, out, retry)
        ctx.console.info(f"\nDone. All files are in {out}")
        return res
    finally:
        logging.getLogger("ai_switch").removeHandler(handler)
        handler.close()


def export_only(ctx: Context, source: str, *, with_chats: bool = False) -> RunResult:
    source = resolve(source)
    out = make_out_dir(ctx.out_dir, f"{source}-backup")
    handler = attach_run_log(out)
    try:
        ctx.console.step(1, 1, f"Export from {get_adapter(source).name}")
        memory, chats, src = _guard("Export", export_step, ctx, source, with_chats, out)
        ctx.console.info(f"\nBackup written to {out}")
        return RunResult(out, memory, chats, export_method=src.last_method.value if src.last_method else "")
    finally:
        logging.getLogger("ai_switch").removeHandler(handler)
        handler.close()


def import_only(ctx: Context, target: str, memory_path: Path, chats_path: Path | None = None,
                redact: str | None = None, verify: bool = True, retry: bool | None = None) -> RunResult:
    target = resolve(target)
    memory = MemoryDoc.load(memory_path)
    chats = ChatArchive.load(chats_path) if chats_path else None
    out = make_out_dir(ctx.out_dir, f"import-{target}")
    handler = attach_run_log(out)
    res = RunResult(out)
    try:
        ctx.console.step(1, 3, "Privacy review")
        memory, chats, res.removed = _guard("Privacy review", privacy_step, ctx, memory, chats, out, redact)
        ctx.console.step(2, 3, f"Import into {get_adapter(target).name}")
        res.imported, res.chats_imported, tgt = _guard("Import", import_step, ctx, target, memory, chats, out)
        if verify:
            ctx.console.step(3, 3, "Verify")
            res.report = _guard("Verify", verify_step, ctx, tgt, memory, out, retry)
        res.memory, res.chats = memory, chats
        return res
    finally:
        logging.getLogger("ai_switch").removeHandler(handler)
        handler.close()


def verify_only(ctx: Context, target: str, memory_path: Path, retry: bool | None = None) -> RunResult:
    target = resolve(target)
    memory = MemoryDoc.load(memory_path)
    out = make_out_dir(ctx.out_dir, f"verify-{target}")
    adapter = get_adapter(target, _ctx_for(ctx, out_dir=out))
    ctx.console.step(1, 1, f"Verify {adapter.name}")
    report = _guard("Verify", verify_step, ctx, adapter, memory, out, retry)
    return RunResult(out, memory, report=report)
