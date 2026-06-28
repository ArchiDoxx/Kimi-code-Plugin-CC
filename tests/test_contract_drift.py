"""Contract-drift tests: plugin surface (skills/commands/agents) vs src/.

These tests guard against the class of drift where a skill/command/agent
references an MCP tool name or verdict vocabulary that does not match the
actual implementation (see ADR / review AR-02, AR-09). They are cheap and
prevent the whole class of "the markdown lied about the contract" bugs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kimi_code_plugin_cc.loops.review import ReviewVerdict
from kimi_code_plugin_cc.loops.santa import SantaVerdict
from kimi_code_plugin_cc.mcp_server import create_server

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_SURFACE_DIRS = ("skills", "commands", "agents")

# MCP tool names exposed by the server. Resolved at runtime from create_server()
# so this test breaks loudly if a tool is renamed/removed.
_EXPECTED_MCP_TOOL_NAMES = {
    "run_agent",
    "run_review_loop",
    "run_santa_loop",
    "run_planning_loop",
}

# Verdict vocabularies that the loops actually parse/produce.
_REVIEW_VERDICTS = {v.value for v in ReviewVerdict}
_SANTA_VERDICTS = {v.value for v in SantaVerdict}


def _surface_files() -> list[Path]:
    files: list[Path] = []
    for sub in PLUGIN_SURFACE_DIRS:
        d = ROOT / sub
        if d.exists():
            files.extend(d.rglob("*.md"))
    return files


def _read_surface() -> dict[str, str]:
    return {str(p): p.read_text(encoding="utf-8") for p in _surface_files()}


def test_mcp_tool_names_in_surface_exist() -> None:
    """Every mcp__...__<tool> reference in the surface must match a real tool.

    Catches the case where a skill mentions a tool that no longer exists (or
    was renamed), which would silently break the slash-command path.
    """
    surface = _read_surface()
    tool_ref_re = re.compile(r"mcp__kimi-code-plugin-cc__([a-z_]+)")
    referenced: set[str] = set()
    for _path, text in surface.items():
        for match in tool_ref_re.finditer(text):
            referenced.add(match.group(1))
    # Tools referenced in the surface must all be real exposed tools.
    missing = referenced - _EXPECTED_MCP_TOOL_NAMES
    assert not missing, (
        f"Plugin surface references MCP tools that are not exposed by the "
        f"server: {sorted(missing)}. Exposed: {sorted(_EXPECTED_MCP_TOOL_NAMES)}."
    )


@pytest.mark.asyncio
async def test_server_exposes_expected_tool_set() -> None:
    """create_server() must expose exactly the expected 4 tools.

    If a tool is added/removed without updating the surface references, this
    test (combined with the one above) forces a deliberate decision.
    """
    server = create_server()
    # FastMCP exposes registered tools via _tool_manager; read their names.
    tools = await server.list_tools()
    exposed = {t.name for t in tools}
    assert exposed == _EXPECTED_MCP_TOOL_NAMES, (
        f"MCP server tool set drifted. Expected {sorted(_EXPECTED_MCP_TOOL_NAMES)}, "
        f"got {sorted(exposed)}."
    )


def test_verdict_vocabularies_match_surface() -> None:
    """Agent/skill files must use the verdict vocabulary the loops parse.

    The review-adversary agent and review/santa skills must reference the
    verdicts the loops actually recognise (approve/request_changes/
    needs_discussion for per-reviewer verdicts; green/red for the final santa
    result). Catches the AR-02 drift where agents used green/yellow/red as
    per-reviewer verdicts that the parser could never match.
    """
    surface = _read_surface()
    # Per-reviewer verdicts the loops parse.
    per_reviewer = _REVIEW_VERDICTS  # approve/request_changes/needs_discussion
    # Final santa result verdicts.
    santa_final = _SANTA_VERDICTS  # green/red

    # The review-adversary agent is a per-reviewer role: its VERDICT line must
    # use the per-reviewer vocabulary, NOT the final santa vocabulary.
    adversary_path = next((p for p in surface if "review-adversary" in p), None)
    assert adversary_path is not None, "agents/review-adversary.md not found"
    adversary_text = surface[adversary_path]

    # The adversary's VERDICT line must mention at least the per-reviewer
    # verdicts (it is a reviewer, not the final arbiter).
    for verdict in per_reviewer:
        assert verdict in adversary_text, (
            f"review-adversary.md does not mention per-reviewer verdict "
            f"{verdict!r}. Per-reviewer vocabulary: {sorted(per_reviewer)}."
        )

    # The adversary must NOT advertise green/yellow/red as its own verdict
    # (that is the final santa result, computed from both reviewers).
    for forbidden in ("yellow",):
        # green/red may appear in explanatory text but not as a VERDICT option.
        # We check the VERDICT instruction block specifically.
        verdict_block = re.search(r"VERDICT:.*?(?=\n\n|\Z)", adversary_text, re.DOTALL)
        if verdict_block:
            assert forbidden not in verdict_block.group(0), (
                f"review-adversary.md VERDICT block uses {forbidden!r}, which "
                f"is not a per-reviewer verdict ({sorted(per_reviewer)})."
            )

    # The santa skill must document both vocabularies correctly: per-reviewer
    # verdicts feed into the final green/red result.
    santa_skill_path = next(
        (p for p in surface if "santa-loop" in p and "SKILL" in p), None
    )
    if santa_skill_path:
        santa_text = surface[santa_skill_path]
        # The final result verdicts (green/red) must be referenced.
        for verdict in santa_final:
            assert verdict in santa_text, (
                f"santa-loop skill does not mention final verdict {verdict!r}."
            )
