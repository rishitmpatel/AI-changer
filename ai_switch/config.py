"""Per-provider configuration (URLs, page selectors, size limits, manual steps).

Lookup order, later wins (deep-merged):

1. ``DEFAULT_CONFIG`` inside the adapter module (lets a new provider be a single file)
2. ``ai_switch/selectors/<provider>.yaml`` shipped with the package
3. ``<app data>/fixes/<provider>.yaml`` – downloaded by "Update fixes" (sanitised, see fixes.py)
4. ``<app data>/selectors/<provider>.yaml`` – your own local edits; always win

App data lives in ``%APPDATA%\\AI Switch`` (Windows), ``~/Library/Application Support/AI Switch``
(macOS) or ``~/.ai-switch`` (Linux). Set ``AI_SWITCH_HOME`` to put it elsewhere.
"""
from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .errors import AiSwitchError

PACKAGE_SELECTORS = Path(__file__).parent / "selectors"


def home_dir() -> Path:
    if os.environ.get("AI_SWITCH_HOME"):
        return Path(os.environ["AI_SWITCH_HOME"])
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "AI Switch"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AI Switch"
    return Path.home() / ".ai-switch"


def user_selectors_dir() -> Path:
    return home_dir() / "selectors"


def fixes_dir() -> Path:
    return home_dir() / "fixes"


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "AI Switch"


def profile_dir() -> Path:
    """Persistent browser profile shared by all providers, so you log in once per site."""
    return home_dir() / "browser-profile"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AiSwitchError(f"{path} is not valid YAML: {exc}", step="Loading selectors",
                            hint="Fix the indentation/quotes on the line mentioned above, or delete your override file.")
    if not isinstance(data, dict):
        raise AiSwitchError(f"{path} must contain a mapping", step="Loading selectors")
    return data


@dataclass
class ProviderConfig:
    provider: str
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)

    # -- accessors
    def url(self, key: str) -> str | None:
        return (self.data.get("urls") or {}).get(key)

    def selector(self, key: str) -> list[str]:
        value = (self.data.get("selectors") or {}).get(key)
        if value is None:
            return []
        return [value] if isinstance(value, str) else [str(v) for v in value]

    def limit(self, key: str, default: int) -> int:
        return int((self.data.get("limits") or {}).get(key, default))

    def manual(self, key: str) -> str:
        return (self.data.get("manual_steps") or {}).get(key, "")

    @property
    def display_name(self) -> str:
        return self.data.get("display_name", self.provider)

    @property
    def import_style(self) -> str:
        return (self.data.get("import") or {}).get("style", "block")

    @property
    def allowed_domains(self) -> list[str]:
        domains = list(self.data.get("allowed_domains") or [])
        for u in (self.data.get("urls") or {}).values():
            host = urlparse(str(u)).hostname
            if host:
                domains.append(host)
        return sorted(set(domains))

    def host_allowed(self, host: str) -> bool:
        host = (host or "").lower().rstrip(".")
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)


def load_config(provider: str, defaults: dict[str, Any] | None = None) -> ProviderConfig:
    data = copy.deepcopy(defaults or {})
    sources: list[Path] = []
    for path in (PACKAGE_SELECTORS / f"{provider}.yaml", fixes_dir() / f"{provider}.yaml",
                 user_selectors_dir() / f"{provider}.yaml"):
        if path.is_file():
            data = deep_merge(data, _load_yaml(path))
            sources.append(path)
    return ProviderConfig(provider, data, sources)
