"""MCP server exposing the agent bridge as tools."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from mcp.server import FastMCP

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.protocol.messages import AgentMessage
from kimi_code_plugin_cc.security.policy import resolve_effective_policy

logger = logging.getLogger(__name__)

SERVER_NAME = "kimi-code-plugin-cc"


def create_server() -> FastMCP:
    """Factory for the FastMCP server instance."""
    server = FastMCP(SERVER_NAME)

    @server.tool()
    def run_agent(
        agent_name: str,
        prompt: str,
        approval_policy: str = "read-only",
    ) -> str:
        """Run a registered agent with *prompt* and return its payload.

        Args:
            agent_name: Registered agent identifier, e.g. ``kimi``.
            prompt: The user prompt to forward to the agent.
            approval_policy: Requested capability level. Capped by ``KIMI_MAX_POLICY``.
        """
        effective_policy = resolve_effective_policy(approval_policy)
        adapter = get(agent_name)
        context: dict[str, Any] = {
            "bridge_id": f"mcp-{agent_name}",
            "depth": 0,
            "approval_policy": effective_policy.to_string(),
        }
        message: AgentMessage = adapter.run(prompt, context)
        return message.payload

    return server


def main() -> None:
    """Entry point for the MCP server script."""
    parser = argparse.ArgumentParser(description="Kimi Code plugin MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport to use",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    server = create_server()
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
