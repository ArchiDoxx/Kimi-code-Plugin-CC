"""Session state and recursion depth tracking."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class SessionState(StrEnum):
    """Lifecycle state of a bridge session."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class Session:
    """Immutable bridge session descriptor."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    depth: int = 0
    turn_count: int = 0
    state: SessionState = SessionState.PENDING

    def __post_init__(self) -> None:
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        if self.turn_count < 0:
            raise ValueError("turn_count must be non-negative")

    def child(self) -> Session:
        """Return a new session representing a nested agent invocation."""
        return Session(
            id=self.id,
            depth=self.depth + 1,
            turn_count=self.turn_count,
            state=SessionState.PENDING,
        )

    def increment_turn(self) -> Session:
        """Return a new session with an incremented turn counter."""
        return Session(
            id=self.id,
            depth=self.depth,
            turn_count=self.turn_count + 1,
            state=self.state,
        )

    def with_state(self, state: SessionState) -> Session:
        """Return a new session with the given lifecycle state."""
        return Session(
            id=self.id,
            depth=self.depth,
            turn_count=self.turn_count,
            state=state,
        )
