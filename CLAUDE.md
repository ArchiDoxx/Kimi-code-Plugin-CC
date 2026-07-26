# CLAUDE.md — Kimi-Code-Plugin-CC

This is a Claude Code plugin that bridges headless CLI agents (starting with
Kimi Code) into Claude Code as external subagents. Kimi Code is used as an
**external reviewer / second opinion** for daily coding tasks. The design is
scalable: add new agent adapters, review loops, and planning loops without
changing the core. **Status: v1.4.0 — integration-ready, verified end-to-end
on Windows.**

## Architecture

- `src/kimi_code_plugin_cc/bridge/` — spawn and parse headless CLI output
  (thread-backed subprocess with `stdin=DEVNULL` + `CREATE_NO_WINDOW` on
  Windows to avoid the Proactor pipe-inheritance block; stdout is streamed and
  the run completes on Kimi's `session.resume_hint` sentinel, then the child
  process tree is killed — `kimi -p` does not exit on its own when global MCP
  servers are configured).
- `src/kimi_code_plugin_cc/protocol/` — Pydantic message schema with depth/bridge IDs.
- `src/kimi_code_plugin_cc/agent_registry/` — adapter registry with two working
  adapters, `kimi` and `codex`. `codex.py` drives `codex exec`: batch, so it
  exits on its own (no sentinel, unlike kimi), and its answer is read from the
  `-o/--output-last-message` file rather than stdout — a missing or empty file
  is a failure even on exit 0. Plugin policy maps to `--sandbox` (`read-only`;
  `workspace-write` only up to `KIMI_MAX_POLICY`), and `explicit` is refused
  because `codex exec` cannot ask a human. Its pinned CLI surface (flags,
  banned capabilities, policy map) lives in `agent_registry/codex_contract.py`,
  which `doctor` and the contract test consume without importing the adapter.
  Guards shared by both adapters (prompt-size cap, model-alias validation,
  Windows `.cmd` de-shimming, PATH resolution) live in
  `agent_registry/common.py` — a second copy of a security guard is a defect,
  not duplication.
- `src/kimi_code_plugin_cc/agent_registry/capabilities.py` — runtime detection of
  what the installed CLI supports, cached per process and fail-safe (unknown =
  not supported). Gate every flag beyond `-p`/`--output-format`/`-m` through it;
  the CLI version is deliberately not pinned.
- `src/kimi_code_plugin_cc/security/` — approval policy, worktree isolation.
- `src/kimi_code_plugin_cc/loops/` — planning, review, and santa-loop logic.
  `loops/prompts.py` holds the shared preamble and verdict contract: **every**
  round builds through it, so no round can drop the contract (that bug made
  iterations 2+ fall back to fuzzy parsing until v1.4.0).
- `src/kimi_code_plugin_cc/transcript.py` - best-effort, fail-open transcript
  persistence for the loops (one `<base>/<run_id>/` directory per run:
  `run.json` plus one `round-NN-<role>.md` per exchange). Configured via
  `KIMI_TRANSCRIPTS` / `KIMI_TRANSCRIPT_DIR` / `KIMI_TRANSCRIPT_KEEP`. Its
  read half is `transcript_view.py` behind `kimi-code-plugin transcripts
  list|show` - strictly read-only, reuses `_RUN_ID_RE`/`_base_dir` from the
  recorder, and reports damaged runs instead of raising.
- `src/kimi_code_plugin_cc/errors.py` — the single error contract for the MCP
  surface. Loop tools return their fail-closed verdict inside the error payload.
- `src/kimi_code_plugin_cc/doctor.py` — preflight checks behind
  `kimi-code-plugin doctor`.
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
- Fail-closed everywhere: unparseable, ambiguous, empty, timed-out and crashed
  reviews all resolve to a non-approval. No failure path may yield `approve` or
  `green` — that includes internal errors, which is why the loop MCP tools carry
  their verdict inside the error payload.

## Development

- Use `uv` for dependency management and scripts.
- Run tests: `uv run pytest`
- Run lint/format: `uv run ruff check . && uv run ruff format .`
- Start MCP server: `uv run kimi-code-plugin-mcp`
- Diagnose the environment: `uv run kimi-code-plugin doctor`

## Verified against

- Kimi Code CLI `@moonshot-ai/kimi-code` **0.29.1** (live-checked 2026-07-25;
  `kimi -p ... --output-format stream-json`; completion detected via the
  terminal `session.resume_hint` event because the process does not exit on
  its own when global MCP servers are configured). The plugin is not pinned to
  this version — `tests/test_cli_contract.py` pins the flag surface instead, and
  newer flags are capability-gated.
- Python ≥ 3.11 (CI: 3.11–3.13 on Linux and Windows).
