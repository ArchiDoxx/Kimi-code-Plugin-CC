"""The pinned ``codex exec`` CLI contract.

What the installed CLI must offer, what the adapter is allowed to emit, and
what it must never emit. Kept separate from the adapter because these are the
facts three different consumers need — the adapter builds argv from them,
``doctor`` checks the installed CLI against them, and
``tests/test_cli_contract.py`` pins them against the real ``codex exec
--help``. Only the adapter cares *how* the CLI is driven.

Everything here was verified live against ``codex-cli 0.145.0`` on 2026-07-26.
Re-verify against the installed CLI before changing any value: this file is the
one place where being wrong from memory turns into a silently broken run or a
weakened sandbox.
"""

from __future__ import annotations

CODEX_EXECUTABLE = "codex"

# Install channel, verified against the project's own README (openai/codex).
INSTALL_HINT = (
    "Install the Codex CLI with 'npm install -g @openai/codex' "
    "(or 'brew install --cask codex'), then run 'codex --version'."
)

# Pinned base surface. Anything not in this list is capability-gated — do not
# add a flag here just because your CLI has it.
#
# Note for future readers: in `codex exec`, `-p` is `--profile`, NOT the prompt
# (the prompt is positional). "Fixing" this by analogy with `kimi -p` would
# silently change which config profile is loaded.
EXEC_SUBCOMMAND = "exec"
SANDBOX_FLAG = "--sandbox"
JSON_FLAG = "--json"
OUTPUT_FLAG = "-o"
MODEL_FLAG = "-m"

# Everything after this is a positional argument. Without it clap parses a
# prompt beginning with '-' as an unknown flag and the run dies before it
# starts (verified: `codex exec --hello` -> "unexpected argument found").
ARGS_SEPARATOR = "--"

# What the contract test and `doctor` look for in `codex exec --help`. The
# *combined* spellings are pinned because the adapter emits the short forms: a
# CLI that kept `--output-last-message` but dropped `-o` would break every run,
# and a long-form-only check would not notice.
REQUIRED_EXEC_FLAGS = (
    "-s, --sandbox",
    "--json",
    "-o, --output-last-message",
    "-m, --model",
)

# Invariant 2 — structural ban on auto-approve. These are codex's equivalents
# of kimi's --yolo and are NEVER emitted, for any input: the strings appear in
# no code path that builds argv, and the adapter's _build_base_command
# re-asserts the sandbox allowlist as defense in depth. The contract test pins
# the names, so an upstream rename breaks the build rather than quietly leaving
# the ban covering nothing.
NEVER_FLAGS = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
)
BANNED_SANDBOX_MODE = "danger-full-access"

# The only sandbox modes the plugin's policy model can produce. An allowlist,
# so a future mapping edit cannot widen the sandbox by accident.
ALLOWED_SANDBOX_MODES = frozenset({"read-only", "workspace-write"})

# Plugin policy -> codex sandbox mode.
#
# 'explicit' is deliberately absent: it means "a human approves each action",
# and `codex exec` is non-interactive, so there is nobody to ask. Recording a
# policy the CLI cannot enact is the defect this repo already refuses for kimi,
# so the adapter raises rather than silently downgrading.
#
# Unlike kimi, 'accept-edits' IS enactable here (`--sandbox workspace-write` is
# a verified flag), so it is honoured — but only up to the KIMI_MAX_POLICY
# ceiling (read-only by default) and only inside the isolated worktree.
POLICY_TO_SANDBOX = {
    "read-only": "read-only",
    "accept-edits": "workspace-write",
}

# Capability-gated flags. --skip-git-repo-check is not optional in practice:
# the isolated worktree is a bare temp directory, and codex refuses to start
# outside a git repository ("Not inside a trusted directory and
# --skip-git-repo-check was not specified", verified live). It only permits a
# run we deliberately want and never widens the sandbox, so it is not part of
# the isolation opt-out below.
SKIP_GIT_REPO_CHECK_FLAG = "--skip-git-repo-check"

# Session isolation makes reviews reproducible and keeps the user's ~/.codex
# session store clean. Caveat: --ignore-user-config also skips config.toml, so
# model aliases or custom providers defined there stop resolving; that surfaces
# as a loud CLI error, and KIMI_CODEX_ISOLATE_SESSION=0 turns it off.
EPHEMERAL_FLAG = "--ephemeral"
IGNORE_USER_CONFIG_FLAG = "--ignore-user-config"
SESSION_ISOLATION_FLAGS = (EPHEMERAL_FLAG, IGNORE_USER_CONFIG_FLAG)
ISOLATION_FLAGS = (*SESSION_ISOLATION_FLAGS, SKIP_GIT_REPO_CHECK_FLAG)
ENV_ISOLATE_SESSION = "KIMI_CODEX_ISOLATE_SESSION"

# The CLI writes the final message into a directory of ours, deliberately
# OUTSIDE the agent's working root: under `workspace-write` a model-generated
# command could otherwise overwrite the very file we treat as the answer.
LAST_MESSAGE_FILENAME = "last-message.txt"
