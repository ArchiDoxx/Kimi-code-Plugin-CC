# CLAUDE.md — Kimi-Code-Plugin-CC

This is a Claude Code plugin that bridges headless CLI agents (starting with Kimi
Code) into Claude Code. It is built for scalability: add new agent adapters,
review loops, and planning loops without changing the core.

## Architecture

- `src/kimi_code_plugin_cc/bridge/` — spawn and parse headless CLI output.
- `src/kimi_code_plugin_cc/protocol/` — Pydantic message schema with depth/bridge IDs.
- `src/kimi_code_plugin_cc/agent_registry/` — adapter registry (Kimi, Codex skeleton).
- `src/kimi_code_plugin_cc/security/` — approval policy, worktree isolation.
- `src/kimi_code_plugin_cc/loops/` — planning, review, and santa-loop logic.
- `src/kimi_code_plugin_cc/mcp_server.py` — MCP server exposing `run_agent`.
- `skills/`, `agents/`, `commands/` — Claude Code plugin surface.

## Safety rules

- Never spawn a CLI agent with `--yolo`/`--auto` unless explicitly approved.
- Default approval policy is `read-only`.
- Every agent runs in an isolated worktree.
- Depth-guard prevents recursive agent swarms (`KIMI_BRIDGE_DEPTH`, default 2).
- Policy escalation above `KIMI_MAX_POLICY` requires human approval.

## Development

- Use `uv` for dependency management and scripts.
- Run tests: `uv run pytest`
- Run lint/format: `uv run ruff check . && uv run ruff format .`
- Start MCP server: `uv run kimi-code-plugin-mcp`
