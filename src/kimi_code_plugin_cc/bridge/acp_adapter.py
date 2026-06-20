"""ACP adapter skeleton for v0.6 Agent Communication Protocol integration.

The Agent Communication Protocol (`kimi acp`) will mediate tool-call permissions
between the host and a long-lived agent process. In v0.5 every turn spawns a fresh
process; this module documents the shape of the future adapter without changing
v0.5 behaviour.
"""

from __future__ import annotations

from typing import Any


class AcpAdapter:
    """Skeleton adapter for a future ACP-based bridge."""

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url

    async def connect(self) -> None:
        """Establish an ACP session with the agent."""
        raise NotImplementedError("ACP integration is planned for v0.6")

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a message to the agent and return its response."""
        raise NotImplementedError("ACP integration is planned for v0.6")

    async def close(self) -> None:
        """Close the ACP session."""
        raise NotImplementedError("ACP integration is planned for v0.6")
