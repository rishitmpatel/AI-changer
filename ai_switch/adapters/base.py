"""Base adapter: the shared interface and the automatic 5-step fallback chain.

Every provider adapter subclasses :class:`BaseAdapter` and usually only sets
``id``/``name``, the method order, and (optionally) a ``parse_export`` override.
Everything site-specific — URLs, selectors, click-by-click flows, size limits,
manual instructions — lives in ``selectors/<id>.yaml``.

Fallback order (per the design):
  1. EXPORT_FILE    parse the official zip/json the user downloaded       (no network)
  2. OFFICIAL_FLOW  drive the provider's own memory/import screen          (browser)
  3. ASK_AI         ask the AI to print / remember the memory in a chat    (browser)
  4. BROWSER        fill or scrape settings fields with page selectors     (browser)
  5. CLIPBOARD      copy text, pause, tell the user exactly what to click  (manual)
"""
from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, ContextManager

from ..browser import BrowserSession
from ..clipboard import Clipboard
from ..config import ProviderConfig, load_config
from ..errors import AiSwitchError, MethodFailed, MethodUnavailable, StepError
from ..exports import ExportBundle, ParsedExport, parse_generic, write_chatgpt_zip
from ..extract import ASK_MEMORY_PROMPT, END_MARK, VERIFY_PROMPT, facts_from_strings, mine_facts_from_chats, \
    parse_memory_text
from ..render import build_import_parts, chats_markdown
from ..schema import ChatArchive, MemoryDoc, write_private
from ..ui import Console

log = logging.getLogger("ai_switch")


class Method(str, Enum):
    EXPORT_FILE = "export_file"
    OFFICIAL_FLOW = "official_flow"
    ASK_AI = "ask_ai"
    BROWSER = "browser"
    CLIPBOARD = "clipboard"

    @property
    def label(self) -> str:
        return {"export_file": "1 official export file", "official_flow": "2 official in-app flow",
                "ask_ai": "3 ask-the-AI prompt", "browser": "4 browser automation",
                "clipboard": "5 clipboard + manual click"}[self.value]

    @property
    def needs_browser(self) -> bool:
        return self in (Method.OFFICIAL_FLOW, Method.ASK_AI, Method.BROWSER)


ALL_METHODS = tuple(Method)
BrowserFactory = Callable[[ProviderConfig], ContextManager[BrowserSession]]


@dataclass
class Context:
    """Runtime services handed to adapters (swapped for fakes in tests)."""

    console: Console = field(default_factory=Console)
    clipboard: Clipboard = field(default_factory=Clipboard)
    browser_factory: BrowserFactory | None = None
    export_path: Path | None = None
    out_dir: Path = Path("ai-switch-output")
    offline: bool = False
    headless: bool = False
    strict_network: bool = True
    only_methods: tuple[Method, ...] | None = None
    max_chats: int = 50
    #: BrowserManager for the user's own Edge/Chrome (CDP mode). None = Playwright launches its own.
    browser_manager: Any = None
    #: Combine every automatic export method that works (export file + memory page + ask the AI)
    #: instead of stopping at the first success. The desktop app turns this on.
    auto_merge: bool = False
    #: Called with the BrowserSession when a CAPTCHA/human check appears; must return once solved.
    on_challenge: Callable[[Any], None] | None = None

    def open_browser(self, config: ProviderConfig) -> ContextManager[BrowserSession]:
        if self.offline:
            raise MethodUnavailable("browser methods are disabled by --offline")
        if self.browser_factory:
            return self.browser_factory(config)
        if self.browser_manager is not None:
            return BrowserSession(config, manager=self.browser_manager, strict_network=self.strict_network,
                                  on_challenge=self.on_challenge)
        return BrowserSession(config, headless=self.headless, strict_network=self.strict_network,
                              on_challenge=self.on_challenge)


@dataclass
class Attempt:
    method: Method
    ok: bool
    reason: str = ""
    hint: str = ""


@dataclass
class ImportResult:
    method: Method
    parts: int
    manual: bool = False
    note: str = ""


class BaseAdapter:
    id: str = ""
    name: str = ""
    DEFAULT_CONFIG: dict[str, Any] = {}

    export_memory_methods: tuple[Method, ...] = ALL_METHODS
    import_memory_methods: tuple[Method, ...] = (Method.OFFICIAL_FLOW, Method.ASK_AI, Method.BROWSER, Method.CLIPBOARD)
    export_chats_methods: tuple[Method, ...] = (Method.EXPORT_FILE, Method.BROWSER, Method.CLIPBOARD)
    import_chats_methods: tuple[Method, ...] = (Method.OFFICIAL_FLOW, Method.CLIPBOARD)
    #: Whether the provider keeps memory across chats at all (DeepSeek does not).
    has_memory: bool = True

    def __init__(self, ctx: Context | None = None) -> None:
        self.ctx = ctx or Context()
        self.config = load_config(self.id, self.DEFAULT_CONFIG)
        self.attempts: list[Attempt] = []
        self.last_method: Method | None = None
        self._parsed: ParsedExport | None = None
        self._weak_memory: MemoryDoc | None = None
        self.merged_methods: list[Method] = []
        self.last_error: AiSwitchError | None = None

    # ================================================================== public interface
    def export_memory(self) -> MemoryDoc:
        step = f"Export memory from {self.name}"
        if self.ctx.auto_merge:
            automatic = [m for m in (Method.EXPORT_FILE, Method.OFFICIAL_FLOW, Method.ASK_AI)
                         if m in self.export_memory_methods and (m is not Method.EXPORT_FILE or self.ctx.export_path)]
            merged = MemoryDoc(self.id)
            used: list[Method] = []
            for method in automatic:
                doc = self._try("export_memory", method)
                if doc is not None and len(doc):
                    merged.extend(doc)
                    used.append(method)
            if used:
                self.last_method, self.merged_methods = used[0], used
                return merged
            rest = tuple(m for m in self.export_memory_methods if m not in automatic)
            return self._run("export_memory", rest, step=step)
        return self._run("export_memory", self.export_memory_methods, step=step)

    def import_memory(self, doc: MemoryDoc) -> ImportResult:
        if not len(doc):
            raise StepError("there are no facts to import", step=f"Import into {self.name}",
                            hint="Check memory.json; the privacy review may have removed everything.")
        return self._run("import_memory", self.import_memory_methods, doc, step=f"Import memory into {self.name}")

    def export_chats(self) -> ChatArchive:
        return self._run("export_chats", self.export_chats_methods, step=f"Export chats from {self.name}")

    def import_chats(self, chats: ChatArchive) -> ImportResult:
        return self._run("import_chats", self.import_chats_methods, chats, step=f"Import chats into {self.name}")

    def ask(self, prompt: str) -> str:
        """Send one prompt and return the reply (used by verification). Falls back to the clipboard."""
        return self._run("ask", (Method.ASK_AI, Method.CLIPBOARD), prompt, step=f"Ask {self.name}")

    # ================================================================== fallback runner
    def _try(self, op: str, method: Method, *args: Any, tried: list[Attempt] | None = None) -> Any:
        """Run one method; returns its result, or None (recording why) if it is skipped or fails."""
        tried = self.attempts if tried is None else tried
        if self.ctx.only_methods and method not in self.ctx.only_methods:
            return None
        handler = getattr(self, f"_{op}_{method.value}", None)
        if handler is None:
            return None
        if method.needs_browser and self.ctx.offline:
            tried.append(Attempt(method, False, "skipped (--offline)"))
            return None
        try:
            result = handler(*args)
        except (MethodUnavailable, MethodFailed) as exc:
            tried.append(Attempt(method, False, str(exc), exc.hint))
            self.ctx.console.warn(f"{self.name}: method {method.label} → {exc}")
            if exc.hint:
                self.ctx.console.info(f"      hint: {exc.hint}")
            log.info("%s %s via %s failed: %s", self.id, op, method.value, exc)
            self.last_error = exc
            return None
        tried.append(Attempt(method, True))
        log.info("%s %s via %s ok", self.id, op, method.value)
        return result

    def _run(self, op: str, methods: tuple[Method, ...], *args: Any, step: str) -> Any:
        tried: list[Attempt] = []
        for method in methods:
            before = len(tried)
            result = self._try(op, method, *args, tried=tried)
            if len(tried) > before and tried[-1].ok:
                self.attempts += tried
                self.last_method = method
                return result
        self.attempts += tried
        if op == "export_memory" and self._weak_memory is not None and len(self._weak_memory):
            self.ctx.console.warn(f"{self.name}: using {len(self._weak_memory)} facts derived from your chat "
                                  f"history (no direct memory export worked)")
            self.last_method = Method.EXPORT_FILE
            return self._weak_memory
        reasons = "; ".join(f"[{a.method.label}] {a.reason}" for a in tried) or "no method applies"
        hints = [a.hint for a in tried if a.hint]
        raise StepError(f"every method failed — {reasons}", step=step,
                        hint=hints[-1] if hints else "Re-run with --export <file> pointing at the provider's data export.")

    # ================================================================== 1. export file
    def parse_export(self, bundle: ExportBundle) -> ParsedExport:
        """Override for providers with a documented export layout."""
        return parse_generic(bundle, self.id)

    def parsed_export(self) -> ParsedExport:
        if self._parsed is None:
            if not self.ctx.export_path:
                raise MethodUnavailable("no export file given",
                                        hint=f"Download your data ({self.config.manual('export_file') or 'see docs/providers.md'}) "
                                             f"and pass --export <file>.")
            bundle = ExportBundle.open(self.ctx.export_path)
            try:
                self._parsed = self.parse_export(bundle)
            finally:
                bundle.close()
        return self._parsed

    def _export_memory_export_file(self) -> MemoryDoc:
        parsed = self.parsed_export()
        if len(parsed.memory):
            return parsed.memory
        if len(parsed.chats):
            self._weak_memory = mine_facts_from_chats(parsed.chats, self.id)
            raise MethodFailed(f"the export has chats but no memory section "
                               f"({len(self._weak_memory)} facts derived from chats kept as a fallback)")
        raise MethodFailed("the export contains no memory")

    def _export_chats_export_file(self) -> ChatArchive:
        parsed = self.parsed_export()
        if not len(parsed.chats):
            raise MethodFailed("the export contains no conversations")
        return parsed.chats

    # ================================================================== flows from selectors.yaml
    def flow(self, name: str) -> list[Any]:
        steps = (self.config.data.get("flows") or {}).get(name)
        if not steps:
            raise MethodUnavailable(f"no '{name}' flow configured for {self.name}")
        return steps

    def run_flow(self, browser: BrowserSession, name: str, variables: dict[str, Any] | None = None) -> list[str]:
        """Execute a list of steps from selectors.yaml; returns any collected text."""
        variables = variables or {}
        collected: list[str] = []
        for raw in self.flow(name):
            action, arg = (next(iter(raw.items())) if isinstance(raw, dict) else (raw, None))
            if action == "goto":
                browser.goto(arg)
            elif action == "click":
                browser.click(arg)
            elif action == "click_optional":
                browser.click(arg, required=False)
            elif action == "fill":
                browser.fill(arg["into"], str(arg.get("text", "{part}")).format(**variables))
            elif action == "append":
                el = browser.find(arg["into"])
                existing = ""
                with contextlib.suppress(Exception):
                    existing = el.input_value()
                text = str(arg.get("text", "{part}")).format(**variables)
                browser.fill(arg["into"], (existing.rstrip() + "\n" + text).strip() if existing else text)
            elif action == "press":
                browser.page.keyboard.press(arg)
            elif action == "wait":
                browser.page.wait_for_timeout(int(float(arg) * 1000))
            elif action == "wait_for":
                browser.find(arg)
            elif action == "upload":
                browser.upload(arg["into"], Path(str(arg.get("file", "{file}")).format(**variables)))
            elif action == "collect":
                collected += browser.texts(arg)
            elif action == "collect_value":
                el = browser.find(arg, required=False)
                if el is not None:
                    with contextlib.suppress(Exception):
                        value = el.input_value()
                        if value.strip():
                            collected.append(value)
            elif action == "ensure_logged_in":
                browser.ensure_logged_in()
            elif action == "chat":
                browser.send_chat(str(arg).format(**variables), new_chat=False)
                collected.append(browser.wait_reply())
            else:
                raise MethodFailed(f"unknown flow action {action!r} in {self.id}.yaml flows.{name}")
        return collected

    def _browser_flow(self, name: str, variables: dict[str, Any] | None = None, login: bool = True) -> list[str]:
        self.flow(name)  # fail fast (MethodUnavailable) before starting a browser
        with self.ctx.open_browser(self.config) as browser:
            if login:
                browser.ensure_logged_in()
            return self.run_flow(browser, name, variables)

    # ================================================================== 2/3/4 export memory
    def _export_memory_official_flow(self) -> MemoryDoc:
        items = self._browser_flow("export_memory")
        doc = MemoryDoc(self.id)
        for item in items:
            if "\n" in item.strip():
                doc.extend(parse_memory_text(item, self.id, Method.OFFICIAL_FLOW.value))
            else:
                doc.extend(facts_from_strings([(item, None)], self.id, Method.OFFICIAL_FLOW.value))
        if not len(doc):
            raise MethodFailed("the memory page was empty or its selectors no longer match")
        return doc

    def _export_memory_ask_ai(self) -> MemoryDoc:
        if not self.has_memory:
            raise MethodUnavailable(f"{self.name} has no cross-chat memory to ask about")
        with self.ctx.open_browser(self.config) as browser:
            browser.ensure_logged_in()
            reply = browser.ask(ASK_MEMORY_PROMPT, until=END_MARK)
        doc = parse_memory_text(reply, self.id, Method.ASK_AI.value)
        if not len(doc):
            raise MethodFailed(f"{self.name} replied but listed no memories")
        return doc

    def _export_memory_browser(self) -> MemoryDoc:
        items = self._browser_flow("export_profile")
        doc = MemoryDoc(self.id)
        for item in items:
            doc.extend(parse_memory_text(item, self.id, Method.BROWSER.value))
        if not len(doc):
            raise MethodFailed("the profile / instructions fields were empty")
        return doc

    # ================================================================== 5 export memory (manual)
    def _export_memory_clipboard(self) -> MemoryDoc:
        if not self.has_memory:
            raise MethodUnavailable(f"{self.name} has no cross-chat memory; use --export with its chat export")
        console = self.ctx.console
        reply = self._manual_roundtrip(ASK_MEMORY_PROMPT, self.config.manual("export_memory") or
                                       f"Open {self.config.url('new_chat') or self.config.url('home')}, paste the prompt "
                                       f"into a new chat (Ctrl/Cmd+V) and send it. When the answer is complete, click "
                                       f"the Copy button under it.")
        doc = parse_memory_text(reply, self.id, Method.CLIPBOARD.value)
        if not len(doc):
            raise MethodFailed("no memories found in the copied text",
                               hint="Make sure you copied the AI's whole reply (it ends with END-MEMORY).")
        console.ok(f"captured {len(doc)} facts from the clipboard")
        return doc

    def _manual_roundtrip(self, prompt: str, instructions: str) -> str:
        """Copy ``prompt``, tell the user what to do, then read the AI's reply back from the clipboard."""
        console = self.ctx.console
        if not console.interactive:
            raise MethodUnavailable("manual step needs an interactive terminal")
        copied = True
        try:
            self.ctx.clipboard.copy(prompt)
        except Exception:
            copied = False
            path = self.ctx.out_dir / "prompt_to_send.txt"
            write_private(path, prompt)
            console.warn(f"clipboard unavailable; the prompt is in {path}")
        console.pause(("The prompt is on your clipboard. " if copied else "") + instructions)
        reply = ""
        with contextlib.suppress(Exception):
            reply = self.ctx.clipboard.paste()
        if not reply or reply.strip() == prompt.strip():
            reply = console.read_block("Clipboard didn't change. Paste the AI's reply here instead.")
        return reply

    # ================================================================== import memory
    def import_opts(self, method: Method) -> tuple[str, int, int | None]:
        opts = (self.config.data.get("import") or {}).get(method.value) or {}
        style = opts.get("style") or self.config.import_style
        limit = int(opts.get("limit") or self.config.limit("chat_message", 4000))
        item_limit = opts.get("item_limit")
        return style, limit, int(item_limit) if item_limit else None

    def parts_for(self, doc: MemoryDoc, method: Method) -> list[str]:
        style, limit, item_limit = self.import_opts(method)
        parts = build_import_parts(doc, style, limit, item_limit)
        folder = self.ctx.out_dir / "import_parts" / f"{self.id}-{method.value}"
        for i, p in enumerate(parts, 1):
            write_private(folder / f"part-{i:02d}.txt", p)
        return parts

    def _import_memory_official_flow(self, doc: MemoryDoc) -> ImportResult:
        self.flow("import_memory")
        parts = self.parts_for(doc, Method.OFFICIAL_FLOW)
        with self.ctx.open_browser(self.config) as browser:
            browser.ensure_logged_in()
            for i, part in enumerate(parts, 1):
                self.run_flow(browser, "import_memory", {"part": part, "index": i, "total": len(parts)})
                self.ctx.console.ok(f"sent part {i}/{len(parts)} via {self.name}'s import screen")
        return ImportResult(Method.OFFICIAL_FLOW, len(parts))

    def _import_memory_ask_ai(self, doc: MemoryDoc) -> ImportResult:
        style, _, _ = self.import_opts(Method.ASK_AI)
        if not self.config.data.get("memory_from_chat", True) and style != "primer":
            raise MethodUnavailable(f"{self.name} does not save memory from chat requests")
        parts = self.parts_for(doc, Method.ASK_AI)
        with self.ctx.open_browser(self.config) as browser:
            browser.ensure_logged_in()
            for i, part in enumerate(parts, 1):
                browser.send_chat(part, new_chat=(i == 1))
                browser.wait_reply(before_count=i - 1)
                self.ctx.console.ok(f"sent part {i}/{len(parts)} as a chat message")
        return ImportResult(Method.ASK_AI, len(parts))

    def _import_memory_browser(self, doc: MemoryDoc) -> ImportResult:
        self.flow("import_profile")
        parts = self.parts_for(doc, Method.BROWSER)
        with self.ctx.open_browser(self.config) as browser:
            browser.ensure_logged_in()
            for i, part in enumerate(parts, 1):
                self.run_flow(browser, "import_profile", {"part": part, "index": i, "total": len(parts)})
        return ImportResult(Method.BROWSER, len(parts))

    def _import_memory_clipboard(self, doc: MemoryDoc) -> ImportResult:
        console = self.ctx.console
        parts = self.parts_for(doc, Method.CLIPBOARD)
        steps = self.config.manual("import_memory") or f"Open {self.config.url('home')} and paste into a new chat."
        folder = self.ctx.out_dir / "import_parts" / f"{self.id}-clipboard"
        console.info(f"  Manual import into {self.name}: {len(parts)} part(s), also saved in {folder}")
        session = self._open_paste_page()
        try:
            for i, part in enumerate(parts, 1):
                copied = True
                try:
                    self.ctx.clipboard.copy(part)
                except Exception:
                    copied = False
                where = "is on your clipboard" if copied else f"is in {folder / f'part-{i:02d}.txt'}"
                if session is not None:
                    console.wait_for(
                        {"kind": "paste", "provider": self.id, "part": i, "total": len(parts),
                         "title": f"Paste into {self.name}",
                         "text": f"Part {i} of {len(parts)} {where}. Paste it into the box in the browser window "
                                 f"and click Import (or Send) — I'll continue when you do.",
                         "steps": steps},
                        self._paste_detector(session, part))
                else:
                    console.pause(f"Part {i}/{len(parts)} {where}. {steps}")
        finally:
            if session is not None:
                session.__exit__(None, None, None)
        return ImportResult(Method.CLIPBOARD, len(parts), manual=True)

    def _open_paste_page(self) -> Any:
        """Open the target's import screen (or a new chat) for a manual paste; None without a browser."""
        if self.ctx.offline:
            return None
        try:
            session = self.ctx.open_browser(self.config).__enter__()
        except Exception:
            return None
        try:
            url_key = "new_chat" if self.config.url("new_chat") else "home"
            for flow in ("import_memory", "import_profile"):
                for raw in (self.config.data.get("flows") or {}).get(flow) or []:
                    if isinstance(raw, dict) and "goto" in raw:
                        url_key = raw["goto"]
                        break
                else:
                    continue
                break
            session.goto(url_key)
            if getattr(session, "manager", None) is not None:
                session.manager.set_visible(True)
        except Exception:
            pass
        return session

    def _paste_detector(self, session: Any, part: str) -> Callable[[], bool]:
        """True once the user has pasted ``part`` and submitted it (message sent / dialog closed)."""
        lines = [ln[2:].strip() for ln in part.splitlines() if ln.startswith("- ")] or \
                [ln.strip() for ln in part.splitlines() if len(ln.strip()) > 15]
        snippet = (lines[0] if lines else part.strip())[:40]
        boxes = [arg["into"] for flow in ("import_memory", "import_profile")
                 for raw in (self.config.data.get("flows") or {}).get(flow) or []
                 if isinstance(raw, dict) for action, arg in raw.items() if action in ("fill", "append")]
        state = {"armed": False}

        def check() -> bool:
            try:
                if any(snippet in t for t in session.texts("user_message")):
                    return True
                for key in boxes:  # official import box: filled with our text, then submitted/closed
                    el = session.find(key, timeout_ms=0, required=False)
                    value = ""
                    if el is not None:
                        with contextlib.suppress(Exception):
                            value = el.input_value()
                    if snippet in value:
                        state["armed"] = True
                    elif state["armed"]:
                        return True
                body = session.body_text()
                if snippet in body and not state["armed"]:
                    box = session.find("chat_input", timeout_ms=0, required=False)
                    typed = ""
                    if box is not None:
                        with contextlib.suppress(Exception):
                            typed = box.inner_text()
                    return snippet not in typed
            except Exception:
                return False
            return False

        return check

    # ================================================================== chats
    def chats_dir(self) -> Path:
        return self.ctx.out_dir / f"chats_for_{self.id}"

    def write_chat_files(self, chats: ChatArchive) -> dict[str, Path]:
        folder = self.chats_dir()
        files = {"zip": write_chatgpt_zip(chats, folder / "conversations_chatgpt_format.zip")}
        md = folder / "chat_history.md"
        write_private(md, chats_markdown(chats))
        files["markdown"] = md
        for i, conv in enumerate(chats.conversations, 1):
            safe = re.sub(r"[^\w-]+", "_", conv.title)[:50].strip("_") or "chat"
            write_private(folder / "conversations" / f"{i:04d}-{safe}.md",
                          chats_markdown(ChatArchive(chats.source_provider, [conv])))
        return files

    def _import_chats_official_flow(self, chats: ChatArchive) -> ImportResult:
        self.flow("import_chats")
        files = self.write_chat_files(chats)
        self._browser_flow("import_chats", {"file": files["zip"], "markdown": files["markdown"]})
        return ImportResult(Method.OFFICIAL_FLOW, 1, note=f"uploaded {files['zip'].name}")

    def _import_chats_clipboard(self, chats: ChatArchive) -> ImportResult:
        files = self.write_chat_files(chats)
        steps = self.config.manual("import_chats") or (
            f"{self.name} has no chat-history import. Upload {files['markdown'].name} as a file in a new chat "
            f"(or a Project) so it can use it as context.")
        self.ctx.console.pause(f"Chat files are ready in {self.chats_dir()}. {steps}")
        return ImportResult(Method.CLIPBOARD, 1, manual=True, note=str(self.chats_dir()))

    def _export_chats_browser(self) -> ChatArchive:
        from ..schema import Conversation, Message

        self.flow("export_chats")
        chats = ChatArchive(self.id)
        with self.ctx.open_browser(self.config) as browser:
            browser.ensure_logged_in()
            self.run_flow(browser, "export_chats")
            links = []
            for sel in self.config.selector("conversation_link"):
                loc = browser.page.locator(sel)
                links = [h for h in (loc.nth(i).get_attribute("href") for i in range(loc.count())) if h]
                if links:
                    break
            if not links:
                raise MethodFailed("found no conversations in the sidebar",
                                   hint=f"Fix selectors.conversation_link in {self.id}.yaml")
            base = self.config.url("home") or ""
            for n, href in enumerate(dict.fromkeys(links)):
                if n >= self.ctx.max_chats:
                    break
                url = href if href.startswith("http") else base.rstrip("/") + "/" + href.lstrip("/")
                browser.page.goto(url, wait_until="domcontentloaded")
                browser.find("assistant_message")
                users, bots = browser.texts("user_message"), browser.texts("assistant_message")
                msgs = []
                for i in range(max(len(users), len(bots))):
                    if i < len(users):
                        msgs.append(Message("user", users[i]))
                    if i < len(bots):
                        msgs.append(Message("assistant", bots[i]))
                title = (browser.page.title() or "Untitled").strip()
                chats.conversations.append(Conversation(id=href.rstrip("/").split("/")[-1], title=title,
                                                        source_provider=self.id, messages=msgs))
        if not len(chats):
            raise MethodFailed("could not read any conversations")
        return chats

    def _export_chats_clipboard(self) -> ChatArchive:
        console = self.ctx.console
        if not console.interactive:
            raise MethodUnavailable("manual step needs an interactive terminal")
        console.pause(self.config.manual("export_file") or f"Request a data export from {self.name}.")
        path = console.ask("Path to the downloaded export file")
        if not path:
            raise MethodFailed("no export file given")
        self.ctx.export_path = Path(path.strip().strip("'\""))
        self._parsed = None
        return self._export_chats_export_file()

    # ================================================================== ask (verification)
    def _ask_ask_ai(self, prompt: str) -> str:
        with self.ctx.open_browser(self.config) as browser:
            browser.ensure_logged_in()
            return browser.ask(prompt)

    def _ask_clipboard(self, prompt: str) -> str:
        reply = self._manual_roundtrip(prompt, f"Open {self.config.url('new_chat') or self.config.url('home')}, "
                                               f"paste into a NEW chat and send. Then click Copy under the answer.")
        if not reply.strip():
            raise MethodFailed("no answer captured")
        return reply

    # ================================================================== misc
    def login(self) -> None:
        console = self.ctx.console
        with self.ctx.open_browser(self.config) as browser:
            browser.goto("login" if self.config.url("login") else "home")
            console.pause(f"Sign in to {self.name} in the browser window that just opened "
                          f"(passkeys / 2FA are fine). Leave the window open")
            browser.goto("home")
            if browser.find("logged_in", timeout_ms=10000, required=False) is None:
                raise StepError(f"still not signed in to {self.name}", step=f"Login to {self.name}",
                                hint="Finish signing in, then press Enter. If you are signed in, the "
                                     "'logged_in' selector may be outdated — see README.")
        console.ok(f"signed in to {self.name}; the session is saved in the ai-switch browser profile")

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name,
                "export_memory": [m.value for m in self.export_memory_methods],
                "import_memory": [m.value for m in self.import_memory_methods],
                "export_chats": [m.value for m in self.export_chats_methods],
                "import_chats": [m.value for m in self.import_chats_methods]}


__all__ = ["BaseAdapter", "Context", "Method", "ImportResult", "Attempt", "AiSwitchError", "VERIFY_PROMPT"]
