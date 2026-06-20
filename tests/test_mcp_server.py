"""Tests for the MCP server layer."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from kimi_code_plugin_cc.agent_registry import register
from kimi_code_plugin_cc.agent_registry.base import AgentAdapter
from kimi_code_plugin_cc.mcp_server import create_server
from kimi_code_plugin_cc.protocol.messages import AgentMessage


class EchoAdapter(AgentAdapter):
    """Test adapter that echoes the prompt."""

    def __init__(self, name: str = "echo") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        return AgentMessage(
            bridge_id=context.get("bridge_id", ""),
            payload=f"echo: {prompt}",
        )


@pytest.fixture
def echo_adapter() -> EchoAdapter:
    adapter = EchoAdapter("echo")
    register("echo", adapter)
    return adapter


async def test_run_agent_tool(echo_adapter: EchoAdapter) -> None:
    server = create_server()
    with mock.patch.dict(
        "os.environ",
        {"KIMI_MAX_POLICY": "explicit"},
        clear=False,
    ):
        content, _meta = await server.call_tool(
            "run_agent",
            {
                "agent_name": "echo",
                "prompt": "hello",
                "approval_policy": "accept-edits",
            },
        )
    assert len(content) == 1
    assert content[0].text == "echo: hello"


async def test_run_agent_policy_is_capped(echo_adapter: EchoAdapter) -> None:
    server = create_server()
    with mock.patch.dict(
        "os.environ",
        {"KIMI_MAX_POLICY": "read-only"},
        clear=False,
    ):
        content, _meta = await server.call_tool(
            "run_agent",
            {
                "agent_name": "echo",
                "prompt": "hello",
                "approval_policy": "accept-edits",
            },
        )
    assert content[0].text == "echo: hello"


async def test_run_agent_unknown_agent_raises(echo_adapter: EchoAdapter) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    server = create_server()
    with pytest.raises(ToolError, match="No adapter registered"):
        await server.call_tool(
            "run_agent",
            {"agent_name": "missing", "prompt": "hello"},
        )
