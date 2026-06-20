"""Agent registry for headless CLI adapters."""

from __future__ import annotations

from kimi_code_plugin_cc.agent_registry.base import AgentAdapter
from kimi_code_plugin_cc.agent_registry.codex import CodexAdapter
from kimi_code_plugin_cc.agent_registry.kimi import KimiCodeAdapter

__all__ = [
    "AgentAdapter",
    "CodexAdapter",
    "KimiCodeAdapter",
    "get",
    "list_adapters",
    "register",
]

_REGISTRY: dict[str, AgentAdapter] = {}


def _bootstrap() -> None:
    """Register the built-in adapters on import."""
    register("kimi", KimiCodeAdapter())
    register("codex", CodexAdapter())


def register(name: str, adapter: AgentAdapter) -> None:
    """Register an agent adapter under ``name``.

    Raises:
        TypeError: If ``adapter`` is not an ``AgentAdapter`` instance.
    """
    if not isinstance(adapter, AgentAdapter):
        raise TypeError(f"Expected AgentAdapter, got {type(adapter).__name__}")
    _REGISTRY[name] = adapter


def get(name: str) -> AgentAdapter:
    """Return the adapter registered under ``name``.

    Raises:
        KeyError: If no adapter is registered for ``name``.
    """
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"No adapter registered for {name!r}") from exc


def list_adapters() -> list[str]:
    """Return a sorted list of registered adapter names."""
    return sorted(_REGISTRY.keys())


_bootstrap()
