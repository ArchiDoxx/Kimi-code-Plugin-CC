# Kimi-Code-Plugin-CC

A Claude Code plugin that brings headless CLI agents (Kimi Code, Codex, …) into
Claude Code as first-class subagents. It supports review loops, planning loops,
and adversarial dual-review (santa-loop) with a security-first design.

## Features (v0.5)

- Spawn Kimi Code headlessly via `kimi -p ... --output-format stream-json`
  (verified against Kimi Code 0.18.0).
- Extensible agent registry (Kimi concrete, Codex skeleton).
- Single async execution path: adapters `await` one shared, depth-guarded
  runner, so the same code works in tests and inside the MCP event loop.
- Message protocol with recursion depth-guard.
- Read-only default policy, isolated worktrees (under the system temp dir),
  policy ceiling via `KIMI_MAX_POLICY`.
- Robust stream-json parsing: handles single-event, multi-event `tool_calls`,
  and plain-prose output; empty output fails safe (never reads as approval).
- Skills: `bridge`, `planning-loop`, `review-loop`, `santa-loop`.
- MCP server exposing `run_agent`, `run_review_loop`, `run_santa_loop`, and
  `run_planning_loop`.

## Install (development)

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Install in Claude Code

```
/plugin marketplace add ./
/plugin install kimi-code-plugin-cc
```

## Usage

```
/kimi-run "Explain the bridge module"
/kimi-review src/myfile.py
```

## MCP server

Start the MCP server manually:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} kimi-code-plugin-mcp
```

The server exposes four tools:

- `run_agent(agent_name, prompt, approval_policy="read-only")` — one-shot run.
- `run_review_loop(agent_name, target, max_iterations=3)` — iterative review.
- `run_santa_loop(primary_agent, target, max_iterations=3)` — fail-closed
  adversarial dual-review.
- `run_planning_loop(agent_name, prompt, max_iterations=3)` — iterative plan.

`approval_policy` is capped against the `KIMI_MAX_POLICY` environment variable.
For review/planning, pass file **contents** as the target/prompt: the agent runs
in an isolated worktree and cannot open arbitrary host paths.

## Live smoke-test steps

1. Ensure `kimi` CLI is installed and authenticated.
2. Install the plugin in Claude Code (see above).
3. Run a simple round-trip:
   ```
   /kimi-run kimi "Return the word 'pong'"
   ```
4. Verify the response contains `pong` and no `--yolo` / `--auto` flags were used.
5. Run an adversarial review:
   ```
   /kimi-review src/kimi_code_plugin_cc/security/policy.py --loop santa
   ```
6. Verify the loop returns a verdict and respects the fail-closed rule.

## Running tests

```bash
uv run pytest
uv run pytest --cov=kimi_code_plugin_cc --cov-report=term-missing
```

## License

MIT
