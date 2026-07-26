"""Helpers shared by every CLI agent adapter.

These started life as private functions inside the kimi adapter. They are not
kimi-specific: every adapter passes its prompt as an argv element, resolves an
executable that may be a Windows npm shim, and accepts a model alias that must
never be able to turn into a flag. Duplicating them per adapter would let the
copies drift — and these are exactly the guards whose drift is a security
defect rather than a cosmetic one.

Nothing here knows about a particular CLI. Per-agent details (flag names,
install channels, isolation switches) stay in the adapter modules.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from kimi_code_plugin_cc.errors import AgentNotInstalledError

# Prompt size guard. The prompt travels as an argv element, and Windows caps a
# whole CreateProcess command line at 32767 characters. Callers are told to
# paste file *contents* (agents run in an empty worktree and cannot open host
# paths), so oversized prompts are easy to hit — and without this guard they
# surface as an opaque OSError from the spawn instead of an actionable message.
# The default leaves headroom for the executable path and the remaining flags.
ENV_MAX_PROMPT_CHARS = "KIMI_MAX_PROMPT_CHARS"
DEFAULT_MAX_PROMPT_CHARS = 30_000

# Model aliases. Multi-provider setups route providers through config aliases
# like "glm-4.6", "kimi-for-coding" or "gpt-5-codex". The charset is
# conservative and the first character must be alphanumeric, so a model value
# can never smuggle a CLI flag into the argv.
_MODEL_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")

_FALSEY = frozenset({"0", "false", "no", "off"})

# Finds a node entry-point (.mjs/.js/.cjs) referenced inside a .cmd/.bat npm
# shim. npm shims look like: ... "%dp0%\node_modules\<pkg>\dist\main.mjs" %*
# We capture the path after the leading quote (optional) up to the extension.
_SHIM_ENTRY_RE = re.compile(
    r'["\']?(?P<path>(?:[A-Za-z]:[\\/]|[^"\']*?[\\/])?'
    r'node_modules[\\/][^"\']+?\.(?:mjs|js|cjs))["\']?',
    re.IGNORECASE,
)


def env_flag_enabled(name: str, default: bool = True) -> bool:
    """Return whether the boolean env var *name* is on.

    Only explicitly falsey values ("0", "false", "no", "off") switch a flag
    off; anything else counts as on. An unset variable yields *default*.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def max_prompt_chars() -> int:
    """Return the configured prompt ceiling, falling back to the default.

    A non-numeric or non-positive override is ignored rather than allowed to
    disable the guard silently.
    """
    raw = os.environ.get(ENV_MAX_PROMPT_CHARS)
    if raw is None:
        return DEFAULT_MAX_PROMPT_CHARS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PROMPT_CHARS
    return value if value > 0 else DEFAULT_MAX_PROMPT_CHARS


def assert_prompt_fits(prompt: str) -> None:
    """Raise ValueError when *prompt* exceeds the argv budget.

    Raised before anything is spawned so the caller gets an actionable message
    ("review a smaller slice") instead of an opaque spawn failure. ValueError is
    the same class the MCP layer already turns into a structured error.
    """
    limit = max_prompt_chars()
    length = len(prompt)
    if length <= limit:
        return
    raise ValueError(
        f"Prompt is {length} characters, above the {limit}-character limit. "
        "The prompt is passed to the CLI as a command-line argument, which the "
        "operating system caps (32767 characters on Windows). Review a smaller "
        f"slice (one file or hunk at a time), or raise {ENV_MAX_PROMPT_CHARS} "
        "if your platform allows longer command lines."
    )


def validate_model_alias(model: str) -> str:
    """Return *model* if it is a safe CLI model alias, else raise ValueError."""
    if not isinstance(model, str) or _MODEL_ALIAS_RE.fullmatch(model) is None:
        raise ValueError(
            f"Invalid model alias {model!r}: expected a config alias matching "
            "[A-Za-z0-9][A-Za-z0-9._:/-]* (no leading '-', no whitespace or "
            "quotes)"
        )
    return model


def deshim_cmd_wrapper(shim_path: Path) -> list[str] | None:
    """Resolve a Windows ``.cmd``/``.bat`` npm shim to a direct ``node`` argv.

    npm installs ``kimi.CMD`` (and similar) as batch shims that ultimately run
    ``node <pkg>/dist/main.mjs %*`` through ``cmd.exe``. ``cmd.exe`` truncates
    every argument at the first newline and caps lines near 8191 chars, which
    silently breaks multi-line prompts.

    This function reads the shim, locates the node entry-point (a
    ``node_modules/.../*.mjs`` path, resolved relative to the shim's directory
    if it uses ``%dp0%`` / ``%~dp0``), and returns ``["node", <abs entry>]``
    so the caller can invoke node directly via ``CreateProcess``.

    Returns ``None`` if *shim_path* is not a ``.cmd``/``.bat`` file or no
    entry-point could be resolved — callers then fall back to running the
    resolved path as-is (single-line behaviour stays correct).
    """
    if shim_path.suffix.lower() not in (".cmd", ".bat"):
        return None
    try:
        text = shim_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    match = _SHIM_ENTRY_RE.search(text)
    if match is None:
        return None

    raw_path = match.group("path")
    # Resolve npm's %dp0% / %~dp0 (the shim's own directory) if the path is
    # relative to node_modules at the shim's location.
    entry = Path(raw_path)
    if not entry.is_absolute():
        # Strip a possible leading %dp0%-style prefix or backslash.
        cleaned = re.sub(r"^[%~dpDP0\\\/]+", "", raw_path)
        entry = (shim_path.parent / cleaned).resolve()
    else:
        entry = entry.resolve()

    if not entry.exists():
        return None

    # Prefer a node.exe next to the shim (npm layout), else rely on PATH.
    local_node = shim_path.parent / "node.exe"
    node_exec = str(local_node) if local_node.exists() else "node"
    return [node_exec, str(entry)]


def resolve_executable(executable: str, install_hint: str) -> list[str]:
    """Resolve *executable* on PATH, de-shimming Windows ``.cmd``/``.bat``.

    Returns the argv prefix used to invoke the CLI: a single element for a
    native binary, or ``["node", <entry>]`` when PATH resolved to an npm batch
    shim (see :func:`deshim_cmd_wrapper` for why that matters).

    Raises:
        AgentNotInstalledError: If *executable* is not on PATH. The error
            carries *install_hint*, so the caller is told how to install this
            specific agent rather than whichever one happens to be the default.
    """
    resolved = shutil.which(executable)
    if resolved is None:
        raise AgentNotInstalledError(executable, install_hint)
    deshimed = deshim_cmd_wrapper(Path(resolved))
    return deshimed if deshimed is not None else [resolved]
