"""Tests for protocol message models and depth helpers."""

from __future__ import annotations

import pytest

from kimi_code_plugin_cc.protocol.messages import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_POLICY,
    AgentMessage,
    increment_depth,
    is_depth_allowed,
)


def test_agent_message_defaults() -> None:
    msg = AgentMessage(
        bridge_id="bridge-1",
        depth=0,
        payload="hello",
    )
    assert msg.bridge_id == "bridge-1"
    assert msg.depth == 0
    assert msg.approval_policy == DEFAULT_POLICY
    assert msg.payload == "hello"
    assert msg.metadata == {}


def test_agent_message_with_policy() -> None:
    msg = AgentMessage(
        bridge_id="bridge-1",
        depth=0,
        approval_policy="accept-edits",
        payload="hello",
    )
    assert msg.approval_policy == "accept-edits"


def test_agent_message_invalid_policy() -> None:
    with pytest.raises(ValueError):
        AgentMessage(
            bridge_id="bridge-1",
            depth=0,
            approval_policy="invalid-policy",
            payload="hello",
        )


def test_agent_message_negative_depth_rejected() -> None:
    with pytest.raises(ValueError):
        AgentMessage(
            bridge_id="bridge-1",
            depth=-1,
            payload="hello",
        )


def test_agent_message_empty_bridge_id_rejected() -> None:
    with pytest.raises(ValueError):
        AgentMessage(bridge_id="", payload="hello")


def test_agent_message_empty_payload_rejected() -> None:
    with pytest.raises(ValueError):
        AgentMessage(bridge_id="b1", payload="")


def test_increment_depth_returns_message() -> None:
    parent = AgentMessage(
        bridge_id="bridge-1",
        depth=2,
        payload="parent",
    )
    child = increment_depth(parent)
    assert child.depth == 3
    assert child.bridge_id == parent.bridge_id


def test_is_depth_allowed_with_message() -> None:
    msg = AgentMessage(bridge_id="b1", depth=DEFAULT_MAX_DEPTH, payload="x")
    assert is_depth_allowed(msg, DEFAULT_MAX_DEPTH)


def test_is_depth_allowed_with_int() -> None:
    assert is_depth_allowed(0, DEFAULT_MAX_DEPTH)
    assert is_depth_allowed(DEFAULT_MAX_DEPTH, DEFAULT_MAX_DEPTH)
    assert not is_depth_allowed(DEFAULT_MAX_DEPTH + 1, DEFAULT_MAX_DEPTH)


def test_is_depth_allowed_negative_depth() -> None:
    assert not is_depth_allowed(-1, DEFAULT_MAX_DEPTH)
