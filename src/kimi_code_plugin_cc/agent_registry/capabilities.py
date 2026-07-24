"""Runtime capability detection for a headless CLI agent.

The plugin deliberately does **not** pin the agent CLI to a version: it depends
only on a small public flag surface. That decision has a cost — a flag added in
a newer CLI cannot simply be used, because passing an unknown flag to an older
build makes every call fail with a cryptic CLI error.

This module resolves that by asking the installed CLI what it supports, once,
and caching the answer for the life of the process. Detection is fail-safe: if
``--help`` cannot be run or parsed, every capability reports ``False`` and the
caller keeps the older, always-valid command shape.
"""

from __future__ import annotations

import re
import subprocess
import threading

# Windows: don't let the probe flash a console window (same rationale as the
# runner's CREATE_NO_WINDOW).
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# A help probe must never become a second hang; the CLI prints help and exits.
_PROBE_TIMEOUT_SECONDS = 20.0

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

_lock = threading.Lock()
_help_cache: dict[tuple[str, ...], str] = {}
_version_cache: dict[tuple[str, ...], str | None] = {}


def _probe(argv_prefix: tuple[str, ...], flag: str) -> str:
    """Run ``<argv_prefix> <flag>`` and return its combined output ('' on error).

    Returns an empty string on any failure (missing executable, non-zero exit,
    timeout). Callers must treat '' as "nothing detected".
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argv comes from resolved executables
            [*argv_prefix, flag],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            creationflags=_CREATION_FLAGS,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.decode("utf-8", errors="replace")


def help_text(argv_prefix: list[str]) -> str:
    """Return cached ``--help`` output for *argv_prefix* ('' if unavailable)."""
    key = tuple(argv_prefix)
    with _lock:
        cached = _help_cache.get(key)
    if cached is not None:
        return cached
    text = _probe(key, "--help")
    with _lock:
        _help_cache[key] = text
    return text


def supports_flag(argv_prefix: list[str], flag: str) -> bool:
    """Return True when the installed CLI advertises *flag* in its help output.

    Fail-safe: an unreadable help output yields ``False``, so the caller keeps
    the command shape that works on every version.
    """
    return flag in help_text(argv_prefix)


def cli_version(argv_prefix: list[str]) -> str | None:
    """Return the CLI's reported semantic version, or ``None`` if unknown."""
    key = tuple(argv_prefix)
    with _lock:
        if key in _version_cache:
            return _version_cache[key]
    match = _VERSION_RE.search(_probe(key, "--version"))
    version = match.group(0) if match else None
    with _lock:
        _version_cache[key] = version
    return version


def reset_cache() -> None:
    """Clear the capability caches. Intended for tests."""
    with _lock:
        _help_cache.clear()
        _version_cache.clear()
