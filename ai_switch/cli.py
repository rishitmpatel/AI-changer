"""Command-line entry point: ``ai-switch``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .adapters import Context, Method, available, get_adapter
from .errors import AiSwitchError, UserAborted
from .ui import Console

EPILOG = """examples:
  ai-switch login chatgpt
  ai-switch move --from chatgpt --to claude --export ~/Downloads/chatgpt-export.zip
  ai-switch move --from claude --to gemini --chats --export ~/Downloads/claude-data.zip
  ai-switch export --from gemini --export ~/Downloads/takeout.zip
  ai-switch import --to mistral --memory ai-switch-output/…/memory.json
  ai-switch verify --to claude --memory ai-switch-output/…/memory.json
  ai-switch providers
"""


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", type=Path, default=Path("ai-switch-output"), help="output folder (default ./ai-switch-output)")
    p.add_argument("--offline", action="store_true", help="never open a browser: export file + clipboard only")
    p.add_argument("--headless", action="store_true", help="hide the browser window (login must already exist)")
    p.add_argument("--no-strict-network", dest="strict_network", action="store_false",
                   help="let the browser load third-party hosts (default: only the provider's own domains)")
    p.add_argument("--method", action="append", choices=[m.value for m in Method],
                   help="restrict to these fallback methods (repeatable)")
    p.add_argument("--yes", "-y", action="store_true", help="non-interactive: accept defaults, no pauses")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-switch", description="Move AI memory and chat history between assistants.",
                                     epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"ai-switch {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    mv = sub.add_parser("move", help="export → privacy review → import → verify")
    mv.add_argument("--from", dest="source", required=True)
    mv.add_argument("--to", dest="target", required=True)
    mv.add_argument("--export", type=Path, help="the source's official data-export file (zip/json/csv/folder)")
    mv.add_argument("--chats", action="store_true", help="also move chat history")
    mv.add_argument("--redact", help="skip the privacy prompt: all | none | comma list of types (email,phone,address,…)")
    mv.add_argument("--no-verify", dest="verify", action="store_false")
    mv.add_argument("--retry", action="store_true", default=None, help="re-send missing facts without asking")
    mv.add_argument("--max-chats", type=int, default=50, help="max chats to scrape when no export file (default 50)")
    _common(mv)

    ex = sub.add_parser("export", help="backup only: write memory.json / memory.md (and chats)")
    ex.add_argument("--from", dest="source", required=True)
    ex.add_argument("--export", type=Path)
    ex.add_argument("--chats", action="store_true")
    ex.add_argument("--max-chats", type=int, default=50)
    _common(ex)

    im = sub.add_parser("import", help="import an existing memory.json into a provider")
    im.add_argument("--to", dest="target", required=True)
    im.add_argument("--memory", type=Path, required=True)
    im.add_argument("--chats-file", type=Path)
    im.add_argument("--redact")
    im.add_argument("--no-verify", dest="verify", action="store_false")
    im.add_argument("--retry", action="store_true", default=None)
    _common(im)

    ve = sub.add_parser("verify", help='ask the target "What do you know about me?" and compare')
    ve.add_argument("--to", dest="target", required=True)
    ve.add_argument("--memory", type=Path, required=True)
    ve.add_argument("--retry", action="store_true", default=None)
    _common(ve)

    lo = sub.add_parser("login", help="open the browser once to sign in (session is remembered)")
    lo.add_argument("provider")
    lo.add_argument("--no-strict-network", dest="strict_network", action="store_false", default=False,
                    help=argparse.SUPPRESS)

    sub.add_parser("providers", help="list providers and their fallback methods")

    gui = sub.add_parser("gui", help="open the local web GUI (http://127.0.0.1 only)")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    return parser


def make_context(args: argparse.Namespace) -> Context:
    console = Console(interactive=False if getattr(args, "yes", False) else None)
    return Context(console=console, export_path=getattr(args, "export", None),
                   out_dir=getattr(args, "out", Path("ai-switch-output")),
                   offline=getattr(args, "offline", False), headless=getattr(args, "headless", False),
                   strict_network=getattr(args, "strict_network", True),
                   only_methods=tuple(Method(m) for m in args.method) if getattr(args, "method", None) else None,
                   max_chats=getattr(args, "max_chats", 50))


def cmd_providers() -> int:
    rows = [("provider", "export memory", "import memory", "chats out", "chats in")]
    for pid, cls in available().items():
        ad = cls()
        short = lambda ms: ">".join(str(list(Method).index(m) + 1) for m in ms)  # noqa: E731
        rows.append((pid, short(ad.export_memory_methods), short(ad.import_memory_methods),
                     short(ad.export_chats_methods), short(ad.import_chats_methods)))
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print("\nmethods: 1 export file · 2 official in-app flow · 3 ask the AI · 4 browser automation · 5 clipboard/manual")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # ✓/→ symbols on legacy Windows code pages
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    from . import migrate

    try:
        if args.command == "providers":
            return cmd_providers()
        if args.command == "gui":
            from .webgui import serve

            return serve(port=args.port, open_browser=not args.no_open)
        if args.command == "login":
            ctx = Context(console=Console(), strict_network=False)
            get_adapter(args.provider, ctx).login()
            return 0
        ctx = make_context(args)
        if args.command == "move":
            res = migrate.move(ctx, args.source, args.target, with_chats=args.chats, redact=args.redact,
                               verify=args.verify, retry=args.retry)
            return 0 if not res.report or not res.report.missing else 2
        if args.command == "export":
            migrate.export_only(ctx, args.source, with_chats=args.chats)
            return 0
        if args.command == "import":
            res = migrate.import_only(ctx, args.target, args.memory, args.chats_file, redact=args.redact,
                                      verify=args.verify, retry=args.retry)
            return 0 if not res.report or not res.report.missing else 2
        if args.command == "verify":
            res = migrate.verify_only(ctx, args.target, args.memory, retry=args.retry)
            return 0 if not res.report.missing else 2
    except UserAborted:
        print("\nCancelled. Nothing else was sent.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("\nCancelled. Nothing else was sent.", file=sys.stderr)
        return 130
    except AiSwitchError as exc:
        print("\n" + exc.render(), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
