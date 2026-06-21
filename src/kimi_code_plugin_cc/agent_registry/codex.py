"""Codex CLI adapter skeleton — validates the abstraction shape."""

from __future__ import annotations

from kimi_code_plugin_cc.agent_registry.base import AgentAdapter
from kimi_code_plugin_cc.protocol.messages import AgentMessage


class CodexAdapter(AgentAdapter):
    """Skeleton adapter for the OpenAI Codex CLI.

    This is intentionally not implemented in v0.5. It exists to validate that the
    ``AgentAdapter`` abstraction generalises beyond Kimi Code and can accommodate a
    ``codex exec`` style command in the future.
    """

    @property
    def name(self) -> str:
        return "codex"

    async def run(self, prompt: str, context: dict) -> AgentMessage:
        raise NotImplementedError(
            "CodexAdapter is a skeleton for v0.5 and does not spawn processes yet."
        )
