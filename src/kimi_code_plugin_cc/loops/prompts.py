"""Shared prompt building blocks for every loop.

Why this module exists: the verdict contract used to be stated **only** in the
first-round prompt of each loop. Refinement, revision and adversarial rounds
built their prompts independently and silently dropped it, so from iteration 2
onward the reviewer was never asked for the machine-readable ``VERDICT:`` line
and :func:`~kimi_code_plugin_cc.loops.review.extract_verdict` had to fall back
to fuzzy free-text scanning. Centralising the blocks here makes it structurally
impossible for one round to disagree with another about the output contract.
"""

from __future__ import annotations

VERDICT_CHOICES = ("approve", "request_changes", "needs_discussion")

# Prepended to every loop prompt. A headless CLI agent inherits the user's
# global agent instructions (for kimi: ``~/.kimi-code/AGENTS.md``) and any
# auto-discovered skills, which can make it adopt a persona, run a project
# bootstrap routine, or ask for a PR/branch instead of reviewing the inline
# material. This paragraph states the interaction shape explicitly so the
# request survives whatever the host machine has configured globally.
#
# Note: defensive hardening, not a fix for a currently reproducing bug — an A/B
# probe against CLI 0.29.1 showed clean output with and without it. Structural
# isolation (skills sandbox) lives in the adapter; see CHANGELOG 1.4.0.
STANDALONE_PREAMBLE = (
    "This is a STANDALONE inline task. Do NOT load project context, do NOT ask "
    "for a PR, branch, or file paths, and do NOT adopt a team/department "
    "persona. Everything you need is in THIS message.\n\n"
)


def verdict_contract() -> str:
    """Return the machine-readable verdict instruction shared by all rounds."""
    choices = "|".join(VERDICT_CHOICES)
    return (
        "\n\nEnd your reply with a single final line, exactly:\n"
        f"VERDICT: <{choices}>\n"
        "Use that line only once, for your own verdict. Do not repeat or quote "
        "another reviewer's VERDICT line."
    )


def review_prompt(body: str) -> str:
    """Wrap *body* as a full review prompt: preamble + body + verdict contract."""
    return f"{STANDALONE_PREAMBLE}{body}{verdict_contract()}"


def plain_prompt(body: str) -> str:
    """Wrap *body* with the standalone preamble only (no verdict contract).

    Used by the planning loop, which asks for a plan rather than a verdict.
    """
    return f"{STANDALONE_PREAMBLE}{body}"
