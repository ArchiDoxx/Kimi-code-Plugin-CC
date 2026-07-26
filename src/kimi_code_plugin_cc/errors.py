"""One error contract for the MCP tool surface.

Before this module the four MCP tools disagreed about failure: ``run_agent``
caught two exception types and returned a bare ``error: ...`` string, while the
three loop tools caught nothing at all. A missing CLI, an unknown agent name or
a timeout therefore escaped as a raw protocol exception — and for the santa
loop that turned the advertised *fail-closed* behaviour into *fail-crashed*:
the caller got no verdict object at all, so "the tool errored" could be
misread as "no signal" rather than "not approved".

Every failure now maps to a stable :class:`ErrorKind` and a JSON payload that
still carries the fail-closed verdict, so a caller reading ``verdict`` cannot
mistake a crash for an approval.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

# Marks an error returned as plain text (``run_agent`` returns the agent's
# payload verbatim on success, so errors need an unmistakable prefix).
ERROR_PREFIX = "kimi-code-plugin-cc error"


class AgentNotInstalledError(FileNotFoundError):
    """Raised when a specific agent CLI cannot be resolved on PATH.

    A plain ``FileNotFoundError`` carries no clue about *which* agent is
    missing, so the generic hint below can only ever name one CLI. With more
    than one adapter that becomes actively misleading — telling a codex user to
    install kimi. This subclass carries the agent's own install instruction in
    its message, and :func:`classify` then suppresses the generic hint.

    It remains a ``FileNotFoundError`` so existing ``except OSError`` handlers
    and the ``not_installed`` classification keep working unchanged.
    """

    def __init__(self, executable: str, install_hint: str) -> None:
        self.executable = executable
        self.install_hint = install_hint
        super().__init__(
            f"Could not find executable {executable!r} in PATH. {install_hint}"
        )


class ErrorKind(StrEnum):
    """Stable, machine-readable failure categories."""

    NOT_INSTALLED = "not_installed"
    UNKNOWN_AGENT = "unknown_agent"
    NOT_IMPLEMENTED = "not_implemented"
    POLICY_REFUSED = "policy_refused"
    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    AGENT_FAILED = "agent_failed"
    INTERNAL = "internal"


_HINTS = {
    # Fallback only: raised for a bare FileNotFoundError, which cannot say
    # which CLI is missing. Adapters raise AgentNotInstalledError instead and
    # supply their own channel (see _hint_for).
    ErrorKind.NOT_INSTALLED: (
        "Install the agent CLI and make sure it is on PATH "
        "(npm i -g @moonshot-ai/kimi-code), then run 'kimi --version'."
    ),
    ErrorKind.UNKNOWN_AGENT: "Use one of the registered agents, e.g. 'kimi'.",
    ErrorKind.TIMEOUT: (
        "The agent produced no completion event before the deadline. Retry with "
        "a smaller target, or check that the CLI is authenticated."
    ),
}


def _hint_for(kind: ErrorKind, exc: BaseException) -> str | None:
    """Return the hint to append to *exc*'s message, if any.

    An :class:`AgentNotInstalledError` already names its own install channel,
    so appending the generic one would contradict it.
    """
    if isinstance(exc, AgentNotInstalledError):
        return None
    return _HINTS.get(kind)


def classify(exc: BaseException) -> tuple[ErrorKind, str]:
    """Map *exc* to a stable kind and a caller-facing message.

    Ordering matters: :class:`AdapterNotImplementedError` subclasses
    ``NotImplementedError``, and ``FileNotFoundError`` subclasses ``OSError``,
    so the specific cases are tested before the general ones.
    """
    if isinstance(exc, NotImplementedError):
        kind = ErrorKind.NOT_IMPLEMENTED
    elif isinstance(exc, KeyError):
        kind = ErrorKind.UNKNOWN_AGENT
    elif isinstance(exc, PermissionError):
        kind = ErrorKind.POLICY_REFUSED
    elif isinstance(exc, TimeoutError):
        kind = ErrorKind.TIMEOUT
    elif isinstance(exc, FileNotFoundError):
        kind = ErrorKind.NOT_INSTALLED
    elif isinstance(exc, ValueError):
        kind = ErrorKind.INVALID_INPUT
    elif isinstance(exc, RuntimeError):
        kind = ErrorKind.AGENT_FAILED
    else:
        kind = ErrorKind.INTERNAL

    # KeyError's str() is the repr of the key; use the argument directly so the
    # message reads as prose rather than as a quoted key.
    message = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
    hint = _hint_for(kind, exc)
    return kind, f"{message} {hint}".strip() if hint else message


def as_text(exc: BaseException) -> str:
    """Render *exc* as a single-line error string for text-returning tools."""
    kind, message = classify(exc)
    return f"{ERROR_PREFIX} [{kind.value}]: {message}"


def as_json(exc: BaseException, **fail_closed: Any) -> str:
    """Render *exc* as a JSON error payload for the loop tools.

    ``fail_closed`` carries the loop's safe verdict (e.g. ``verdict="red"``) so
    a caller that only reads the verdict field still sees a non-approval.
    """
    kind, message = classify(exc)
    payload: dict[str, Any] = {
        "status": "error",
        "error_kind": kind.value,
        "message": message,
        **fail_closed,
    }
    return json.dumps(payload, indent=2)
