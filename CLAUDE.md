# CLAUDE.md — Kimi-Code-Plugin-CC

This is a Claude Code plugin that bridges headless CLI agents (starting with
Kimi Code) into Claude Code as external subagents. Kimi Code is used as an
**external reviewer / second opinion** for daily coding tasks. The design is
scalable: add new agent adapters, review loops, and planning loops without
changing the core. **Status: v1.3.0 — integration-ready, verified end-to-end
on Windows.**

## Architecture

- `src/kimi_code_plugin_cc/bridge/` — spawn and parse headless CLI output
  (thread-backed subprocess with `stdin=DEVNULL` + `CREATE_NO_WINDOW` on
  Windows to avoid the Proactor pipe-inheritance block; stdout is streamed and
  the run completes on Kimi's `session.resume_hint` sentinel, then the child
  process tree is killed — `kimi -p` does not exit on its own when global MCP
  servers are configured).
- `src/kimi_code_plugin_cc/protocol/` — Pydantic message schema with depth/bridge IDs.
- `src/kimi_code_plugin_cc/agent_registry/` — adapter registry (`kimi` working,
  `codex` skeleton raising `NotImplementedError` in v1.0).
- `src/kimi_code_plugin_cc/security/` — approval policy, worktree isolation.
- `src/kimi_code_plugin_cc/loops/` — planning, review, and santa-loop logic.
- `src/kimi_code_plugin_cc/mcp_server.py` — MCP server exposing `run_agent`,
  `run_review_loop`, `run_santa_loop`, `run_planning_loop`.
- `skills/`, `agents/`, `commands/` — Claude Code plugin surface.

## Safety rules

- Never spawn a CLI agent with `--yolo`/`--auto` unless explicitly approved.
  These flags are structurally never injected by the adapter.
- Default approval policy is `read-only`.
- Every agent runs in an isolated worktree under the system temp dir.
- Depth-guard prevents recursive agent swarms (`KIMI_BRIDGE_DEPTH`, default 2).
- Policy escalation above `KIMI_MAX_POLICY` requires human approval.

## Development

- Use `uv` for dependency management and scripts.
- Run tests: `uv run pytest`
- Run lint/format: `uv run ruff check . && uv run ruff format .`
- Start MCP server: `uv run kimi-code-plugin-mcp`

## Verified against

- Kimi Code CLI `@moonshot-ai/kimi-code` **0.22.2**
  (`kimi -p ... --output-format stream-json`; completion detected via the
  terminal `session.resume_hint` event because the process does not exit on
  its own when global MCP servers are configured).
- Python ≥ 3.11.
