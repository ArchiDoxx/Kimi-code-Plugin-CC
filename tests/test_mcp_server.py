"""Tests for the MCP server layer."""

from __future__ import annotations

import json
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

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        return AgentMessage(
            bridge_id=context.get("bridge_id", ""),
            payload=f"echo: {prompt}",
        )


class StubAdapter(AgentAdapter):
    """Test adapter returning a fixed payload regardless of the prompt."""

    def __init__(self, name: str, payload: str) -> None:
        self._name = name
        self._payload = payload

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        return AgentMessage(
            bridge_id=context.get("bridge_id", self._name),
            payload=self._payload,
        )


class RecordingAdapter(AgentAdapter):
    """Records the call context so tests can assert MCP-level plumbing."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.contexts: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        self.contexts.append(context)
        return AgentMessage(
            bridge_id=context.get("bridge_id", ""),
            payload="ok",
        )


class ValueErrorAdapter(AgentAdapter):
    """Raises ValueError like the real adapter does for an invalid model."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        raise ValueError("Invalid model alias '--yolo'")


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


async def test_run_agent_forwards_model_into_context() -> None:
    adapter = RecordingAdapter("rec-model")
    register("rec-model", adapter)
    server = create_server()
    await server.call_tool(
        "run_agent",
        {"agent_name": "rec-model", "prompt": "p", "model": "glm-4.6"},
    )
    assert adapter.contexts[0]["model"] == "glm-4.6"


async def test_run_agent_without_model_omits_context_key() -> None:
    adapter = RecordingAdapter("rec-no-model")
    register("rec-no-model", adapter)
    server = create_server()
    await server.call_tool(
        "run_agent",
        {"agent_name": "rec-no-model", "prompt": "p"},
    )
    assert "model" not in adapter.contexts[0]


async def test_run_agent_returns_structured_error_on_invalid_model() -> None:
    register("rec-bad-model", ValueErrorAdapter("rec-bad-model"))
    server = create_server()
    content, _meta = await server.call_tool(
        "run_agent",
        {"agent_name": "rec-bad-model", "prompt": "p", "model": "--yolo"},
    )
    assert content[0].text.startswith("error: Invalid model alias")


async def test_run_review_loop_tool_returns_approve() -> None:
    register("stub-review", StubAdapter("stub-review", "approve looks good"))
    server = create_server()
    content, _meta = await server.call_tool(
        "run_review_loop",
        {"agent_name": "stub-review", "target": "src/x.py", "max_iterations": 2},
    )
    payload = json.loads(content[0].text)
    assert payload["verdict"] == "approve"
    assert payload["iterations"] == 1


async def test_run_santa_loop_tool_fail_closes_red() -> None:
    register("stub-santa", StubAdapter("stub-santa", "request_changes real bug"))
    server = create_server()
    content, _meta = await server.call_tool(
        "run_santa_loop",
        {"primary_agent": "stub-santa", "target": "src/sec.py", "max_iterations": 1},
    )
    payload = json.loads(content[0].text)
    assert payload["verdict"] == "red"
    assert "did not approve" in payload["explanation"]


async def test_run_planning_loop_tool_returns_plan() -> None:
    register("stub-plan", StubAdapter("stub-plan", "plan: do step one then two"))
    server = create_server()
    content, _meta = await server.call_tool(
        "run_planning_loop",
        {"agent_name": "stub-plan", "prompt": "design storage", "max_iterations": 1},
    )
    payload = json.loads(content[0].text)
    assert "step one" in payload["plan"]
    assert payload["iterations"] == 1
