"""Approval-policy enforcement and worktree isolation.

The policy model is intentionally ordered from least to most permissive:

    read-only < explicit < accept-edits

- ``read-only``: The agent may observe files but must not modify state.
- ``explicit``: The agent may propose changes, but each change requires
  explicit human approval before execution.
- ``accept-edits``: The agent may execute edits without per-action approval,
  up to the global cap configured via ``KIMI_MAX_POLICY``.

``resolve_effective_policy`` caps any caller-requested policy against the
environment-configured maximum, preventing model-driven escalation.
"""

from __future__ import annotations

import os
import tempfile
from enum import IntEnum
from pathlib import Path

_ENV_MAX_POLICY = "KIMI_MAX_POLICY"
_DEFAULT_POLICY_NAME = "read-only"
_ENV_WORKTREE_BASE = "KIMI_WORKTREE_BASE"


class ApprovalPolicy(IntEnum):
    """Ordered capability levels for agent execution."""

    READ_ONLY = 0
    EXPLICIT = 1
    ACCEPT_EDITS = 2

    @classmethod
    def from_string(cls, value: str) -> ApprovalPolicy:
        """Parse a policy name case-insensitively.

        Raises:
            ValueError: If the supplied string does not match a known policy.
        """
        mapping = {
            "read-only": cls.READ_ONLY,
            "explicit": cls.EXPLICIT,
            "accept-edits": cls.ACCEPT_EDITS,
        }
        normalized = value.strip().lower()
        try:
            return mapping[normalized]
        except KeyError as exc:
            raise ValueError(
                f"Unknown approval policy {value!r}. Choose from: {', '.join(mapping)}."
            ) from exc

    def to_string(self) -> str:
        """Return the canonical kebab-case name for this policy."""
        return self.name.lower().replace("_", "-")


def read_max_policy_from_env() -> ApprovalPolicy:
    """Read ``KIMI_MAX_POLICY`` from the environment, defaulting to read-only."""
    raw = os.environ.get(_ENV_MAX_POLICY, _DEFAULT_POLICY_NAME)
    return ApprovalPolicy.from_string(raw)


def resolve_effective_policy(
    requested: ApprovalPolicy | str,
    max_policy: ApprovalPolicy | str | None = None,
) -> ApprovalPolicy:
    """Cap ``requested`` policy against the configured maximum.

    Args:
        requested: Policy requested by the caller (enum or string).
        max_policy: Override maximum. If omitted, ``KIMI_MAX_POLICY`` is read
            from the environment. Strings are parsed case-insensitively.

    Returns:
        The less permissive of the requested and maximum policies.
    """
    requested_enum = (
        requested
        if isinstance(requested, ApprovalPolicy)
        else ApprovalPolicy.from_string(requested)
    )

    if max_policy is None:
        max_enum = read_max_policy_from_env()
    elif isinstance(max_policy, ApprovalPolicy):
        max_enum = max_policy
    else:
        max_enum = ApprovalPolicy.from_string(max_policy)

    return min(requested_enum, max_enum)


def create_isolated_worktree(base_dir: Path | str | None = None) -> Path:
    """Create a unique, isolated working directory for a single agent turn.

    The directory is created with restrictive default permissions by
    ``tempfile.mkdtemp`` so that write access to the host repository is never
    implicit. By default it lives under the system temp directory (or the
    ``KIMI_WORKTREE_BASE`` override) rather than the host repo, so isolation
    does not litter the working tree.

    Args:
        base_dir: Parent directory. Defaults to ``$KIMI_WORKTREE_BASE`` if set,
            otherwise the system temp directory. Must exist or be creatable.

    Returns:
        Absolute path to the newly created isolated directory.
    """
    if base_dir is None:
        configured = os.environ.get(_ENV_WORKTREE_BASE)
        base = Path(configured) if configured else Path(tempfile.gettempdir())
    else:
        base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    worktree = Path(tempfile.mkdtemp(prefix="kimi_worktree_", dir=base))
    return worktree
