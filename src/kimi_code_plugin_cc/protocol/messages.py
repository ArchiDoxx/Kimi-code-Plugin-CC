"""Pydantic message models for the Kimi Code plugin bridge protocol.

Messages carry a recursion depth counter and an approval policy so the bridge
can enforce the depth guard and capability restrictions across process
boundaries.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ApprovalPolicyLiteral = Literal["read-only", "accept-edits", "explicit"]

DEFAULT_APPROVAL_POLICY: ApprovalPolicyLiteral = "read-only"
DEFAULT_POLICY: ApprovalPolicyLiteral = DEFAULT_APPROVAL_POLICY
DEFAULT_MAX_DEPTH: int = 2


class AgentMessage(BaseModel):
    """A single message exchanged between the host and a spawned CLI agent.

    Attributes:
        bridge_id: Stable identifier for this bridge / conversation.
        depth: Current recursion depth. Must be non-negative.
        approval_policy: Capability level granted to the agent.
        payload: Raw message content from the agent.
        metadata: Optional key-value context (timestamps, tokens, etc.).
    """

    bridge_id: str
    depth: int = Field(default=0, ge=0)
    approval_policy: ApprovalPolicyLiteral = DEFAULT_APPROVAL_POLICY
    payload: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("bridge_id", "payload")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        """Reject empty or whitespace-only identifiers and payloads."""
        if not value.strip():
            raise ValueError("Field must be a non-empty string.")
        return value


def increment_depth(message: AgentMessage) -> AgentMessage:
    """Return a copy of ``message`` with ``depth`` increased by one."""
    return message.model_copy(update={"depth": message.depth + 1})


def to_adapter_context(message: AgentMessage) -> dict[str, Any]:
    """Build the adapter call context from a message.

    Adapters (e.g. :class:`KimiCodeAdapter`) read ``bridge_id``, ``depth`` and
    ``approval_policy`` as **top-level** keys. The loops previously nested these
    under ``{"message": ...}``, so a real adapter silently fell back to defaults
    (losing the conversation ``bridge_id`` and the requested policy). This is the
    single canonical contract shared by the loops and the MCP ``run_agent`` tool.
    """
    return {
        "bridge_id": message.bridge_id,
        "depth": message.depth,
        "approval_policy": message.approval_policy,
    }


def is_depth_allowed(message: AgentMessage | int, max_depth: int) -> bool:
    """Return True when ``depth`` is within ``[0, max_depth]``.

    Accepts either an :class:`AgentMessage` (reads ``message.depth``) or a raw
    ``int`` depth. A negative ``max_depth`` always returns False, matching the
    intuitive expectation that no recursion is permitted.
    """
    depth = message.depth if isinstance(message, AgentMessage) else message
    return 0 <= depth <= max_depth
