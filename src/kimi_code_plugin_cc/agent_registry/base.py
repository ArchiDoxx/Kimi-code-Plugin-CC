"""Abstract base class for CLI agent adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from kimi_code_plugin_cc.protocol.messages import AgentMessage


class AgentAdapter(ABC):
    """Every supported headless CLI agent implements this interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable agent identifier, e.g. ``\"kimi\"`` or ``\"codex\"``."""

    @abstractmethod
    async def run(self, prompt: str, context: dict) -> AgentMessage:
        """Invoke the agent with ``prompt`` and return its response message.

        Adapters are coroutines so they can ``await`` the shared async runner
        directly. This is required because the MCP server executes tools inside
        its event loop, where ``asyncio.run`` cannot be called.

        Args:
            prompt: The user/system prompt for this turn.
            context: Shared context such as ``depth``, ``bridge_id``, or
                ``approval_policy``.
        """
