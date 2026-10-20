"""'Update fixes': fetch the latest selectors/*.yaml from the GitHub repo's main branch.

Downloaded files are untrusted until checked, so a fix can only change *how* a site is
driven, never *where* data may go:

* ``allowed_domains`` is dropped (the packaged list stays in force),
* every URL must point at a host the packaged file already allows,
* only known top-level keys are kept, and the result must still parse and validate.

Only raw.githubusercontent.com (or the URL set in Settings) is contacted, and only on click.
No memory or personal data is sent; a GitHub token is added only if you set one (needed
when the repository is private).
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import yaml

from .config import PACKAGE_SELECTORS, ProviderConfig, fixes_dir, load_config
from .schema import write_private

log = logging.getLogger("ai_switch.fixes")

DEFAULT_SOURCE = "https://raw.githubusercontent.com/rishitmpatel/AI-changer/main/ai_switch/selectors/{provider}.yaml"
ALLOWED_KEYS = {"provider", "display_name", "urls", "selectors", "flows", "limits", "import", "manual_steps",
                "memory_from_chat", "login", "heal"}
MAX_BYTES = 256 * 1024


@dataclass
class FixReport:
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    dropped: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> str:
        parts = []
        if self.updated:
            parts.append(f"updated {len(self.updated)} ({', '.join(self.updated)})")
        if self.unchanged:
            parts.append(f"{len(self.unchanged)} already up to date")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return "; ".join(parts) or "nothing to do"


def sanitize(provider: str, data: Any, packaged: ProviderConfig) -> tuple[dict[str, Any], list[str]]:
    """Return (safe config, list of things removed). Raises ValueError if unusable."""
    if not isinstance(data, dict):
        raise ValueError("not a YAML mapping")
    if data.get("provider", provider) != provider:
        raise ValueError(f"file is for provider {data.get('provider')!r}")
    dropped = [k for k in data if k not in ALLOWED_KEYS]
    clean = {k: v for k, v in data.items() if k in ALLOWED_KEYS}
    urls = clean.get("urls") or {}
    if not isinstance(urls, dict):
        raise ValueError("urls must be a mapping")
    for key, url in list(urls.items()):
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or not packaged.host_allowed(parsed.hostname or ""):
            dropped.append(f"urls.{key}")
            del urls[key]
    for section in ("selectors", "flows", "limits", "import", "manual_steps", "login", "heal"):
        if section in clean and not isinstance(clean[section], dict):
            raise ValueError(f"{section} must be a mapping")
    for name, steps in (clean.get("flows") or {}).items():
        if not isinstance(steps, list):
            raise ValueError(f"flows.{name} must be a list")
    return clean, dropped


def _packaged(provider: str) -> ProviderConfig:
    """The config shipped inside the app (not including earlier downloaded fixes)."""
    path = PACKAGE_SELECTORS / f"{provider}.yaml"
    if path.exists():
        return ProviderConfig(provider, yaml.safe_load(path.read_text("utf-8")) or {})
    return load_config(provider)


def update_fixes(providers: list[str], *, source: str = DEFAULT_SOURCE, token: str = "",
                 opener: Callable[..., Any] = urllib.request.urlopen) -> FixReport:
    report = FixReport()
    out_dir = fixes_dir()
    for provider in providers:
        url = source.format(provider=provider)
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Switch"})
        if token:
            req.add_header("Authorization", f"token {token}")
        try:
            with opener(req, timeout=20) as resp:
                raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("file too large")
            data = yaml.safe_load(raw.decode("utf-8"))
            packaged = _packaged(provider)
            clean, dropped = sanitize(provider, data, packaged)
        except Exception as exc:  # network, 404 (private repo without token), YAML, validation
            msg = str(exc)
            if "404" in msg or "401" in msg:
                msg += " — if the repository is private, add a GitHub token in Settings"
            report.failed[provider] = msg
            log.info("fix for %s failed: %s", provider, type(exc).__name__)
            continue
        if dropped:
            report.dropped[provider] = dropped
        target = out_dir / f"{provider}.yaml"
        text = yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)
        old = target.read_text("utf-8") if target.exists() else None
        packaged_text = yaml.safe_dump(packaged.data, sort_keys=False, allow_unicode=True)
        if old == text or (old is None and text == packaged_text):
            report.unchanged.append(provider)
            continue
        write_private(target, text)
        report.updated.append(provider)
    write_private(out_dir / "last_update.json", json.dumps({"updated": report.updated, "failed": report.failed}))
    return report
