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

## License

MIT
