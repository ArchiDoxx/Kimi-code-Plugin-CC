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
    hint = _HINTS.get(kind)
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
