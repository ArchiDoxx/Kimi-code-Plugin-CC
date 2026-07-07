"""Codex CLI adapter skeleton — validates the abstraction shape."""

from __future__ import annotations

from kimi_code_plugin_cc.agent_registry.base import AgentAdapter
from kimi_code_plugin_cc.protocol.messages import AgentMessage


class AdapterNotImplementedError(NotImplementedError):
    """Raised when a registered adapter is a skeleton, not a usable adapter.

    Distinct from bare :class:`NotImplementedError` so callers (and the MCP
    surface) can produce a helpful message instead of a deep stack trace.
    """


class CodexAdapter(AgentAdapter):
    """Skeleton adapter for the OpenAI Codex CLI.

    This is intentionally not implemented in v1.0. It exists to validate that the
    ``AgentAdapter`` abstraction generalises beyond Kimi Code and can accommodate a
    ``codex exec`` style command in the future. Calling :meth:`run` raises a
    typed :class:`AdapterNotImplementedError`.
    """

    @property
    def name(self) -> str:
        return "codex"

    async def run(self, prompt: str, context: dict) -> AgentMessage:
        raise AdapterNotImplementedError(
            "CodexAdapter is a skeleton for v1.0 and does not spawn processes yet. "
            "Use the 'kimi' agent instead."
        )
