"""Adapter registry. Any module in this package defining a BaseAdapter subclass with an ``id``
is picked up automatically — adding a provider means adding one file here."""
from __future__ import annotations

import importlib
import pkgutil

from ..errors import AiSwitchError
from .base import BaseAdapter, Context, ImportResult, Method

_REGISTRY: dict[str, type[BaseAdapter]] = {}
ALIASES = {"openai": "chatgpt", "gpt": "chatgpt", "anthropic": "claude", "google": "gemini", "bard": "gemini",
           "xai": "grok", "microsoft": "copilot", "msft": "copilot", "pplx": "perplexity", "meta": "meta_ai",
           "metaai": "meta_ai", "meta-ai": "meta_ai", "lechat": "mistral", "le_chat": "mistral",
           "le-chat": "mistral", "vibe": "mistral"}


def _discover() -> None:
    if _REGISTRY:
        return
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_") or mod.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{mod.name}")
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseAdapter) and obj is not BaseAdapter and obj.id:
                _REGISTRY[obj.id] = obj


def register(cls: type[BaseAdapter]) -> type[BaseAdapter]:
    """Register an adapter defined outside this package (e.g. in a test or a private plugin)."""
    _discover()
    _REGISTRY[cls.id] = cls
    return cls


def available() -> dict[str, type[BaseAdapter]]:
    _discover()
    return dict(sorted(_REGISTRY.items()))


def resolve(name: str) -> str:
    _discover()
    key = name.strip().lower().replace(" ", "_")
    key = ALIASES.get(key, key)
    if key not in _REGISTRY:
        raise AiSwitchError(f"unknown provider {name!r}", step="Choosing providers",
                            hint="Known providers: " + ", ".join(_REGISTRY))
    return key


def get_adapter(name: str, ctx: Context | None = None) -> BaseAdapter:
    return available()[resolve(name)](ctx)


__all__ = ["available", "register", "get_adapter", "resolve", "BaseAdapter", "Context", "ImportResult", "Method"]
