"""MCP server exposing the agent bridge and loops as tools."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from mcp.server import FastMCP

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.loops import planning_loop, review_loop, santa_loop
from kimi_code_plugin_cc.protocol.messages import AgentMessage
from kimi_code_plugin_cc.security.policy import resolve_effective_policy

logger = logging.getLogger(__name__)

SERVER_NAME = "kimi-code-plugin-cc"


def create_server() -> FastMCP:
    """Factory for the FastMCP server instance."""
    server = FastMCP(SERVER_NAME)

    @server.tool()
    async def run_agent(
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
        message: AgentMessage = await adapter.run(prompt, context)
        return message.payload

    @server.tool()
    async def run_review_loop(
        agent_name: str,
        target: str,
        max_iterations: int = 3,
    ) -> str:
        """Run a single-agent review loop over *target* and return a verdict.

        The reviewer is asked for a verdict (``approve`` / ``request_changes`` /
        ``needs_discussion``) and the loop stops early on ``approve``.

        Args:
            agent_name: Registered reviewer agent, e.g. ``kimi``.
            target: File path, code block, or description to review.
            max_iterations: Maximum review rounds (default 3).
        """
        result = await review_loop(agent_name, target, max_iterations=max_iterations)
        return result.model_dump_json(indent=2)

    @server.tool()
    async def run_santa_loop(
        primary_agent: str,
        target: str,
        max_iterations: int = 3,
    ) -> str:
        """Run an adversarial dual-review and return a fail-closed verdict.

        The primary agent reviews *target* and an independent, adversarially-
        framed second review is obtained. The result is ``green`` only when
        both reviewers approve; otherwise it is ``red`` (never silently green).

        For a truly heterogeneous second reviewer (Claude reviewing itself),
        run the santa loop from the skill/command layer, which can pass a host
        callback; this MCP tool uses the external-adversary fallback.

        Args:
            primary_agent: Registered adapter for the primary review.
            target: The artifact to review.
            max_iterations: Maximum rounds before fail-closed ``red`` (default 3).
        """
        result = await santa_loop(primary_agent, target, max_iterations=max_iterations)
        return result.model_dump_json(indent=2)

    @server.tool()
    async def run_planning_loop(
        agent_name: str,
        prompt: str,
        max_iterations: int = 3,
    ) -> str:
        """Iteratively build or refine a plan with an external agent.

        Args:
            agent_name: Registered agent to use, e.g. ``kimi``.
            prompt: Task description to plan.
            max_iterations: Maximum refinement rounds (default 3).
        """
        result = await planning_loop(agent_name, prompt, max_iterations=max_iterations)
        return result.model_dump_json(indent=2)

    return server


def main(argv: list[str] | None = None) -> None:
    """Entry point for the MCP server script.

    Args:
        argv: Optional explicit argv (defaults to ``sys.argv[1:]``).
    """
    parser = argparse.ArgumentParser(description="Kimi Code plugin MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport to use",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    server = create_server()
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
