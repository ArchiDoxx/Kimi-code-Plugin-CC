"""Protocol message types for agent communication."""

from .messages import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_POLICY,
    AgentMessage,
    increment_depth,
    is_depth_allowed,
    to_adapter_context,
)

__all__ = [
    "AgentMessage",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_POLICY",
    "increment_depth",
    "is_depth_allowed",
    "to_adapter_context",
]
