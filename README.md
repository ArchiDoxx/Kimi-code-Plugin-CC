> **Work in progress** — v0.5 implementation in progress on branch `feat/v0.5-core`.

# Kimi-Code-Plugin-CC

A Claude Code plugin that brings headless CLI agents (Kimi Code, Codex, …) into
Claude Code as first-class subagents. It supports review loops, planning loops,
and adversarial dual-review (santa-loop) with a security-first design.

## Features (v0.5)

- Spawn Kimi Code headlessly via `kimi -p ... --output-format stream-json`.
- Extensible agent registry (Kimi concrete, Codex skeleton).
- Message protocol with recursion depth-guard.
- Read-only default policy, isolated worktrees, policy ceiling.
- Skills: `bridge`, `planning-loop`, `review-loop`, `santa-loop`.
- MCP server with `run_agent` tool.

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

The server exposes a single tool:

- `run_agent(agent_name: str, prompt: str, approval_policy: str = "read-only")`

`approval_policy` is capped against the `KIMI_MAX_POLICY` environment variable.

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
