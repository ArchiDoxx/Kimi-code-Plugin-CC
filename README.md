# Kimi-Code-Plugin-CC

A Claude Code plugin that brings headless CLI agents (Kimi Code, Codex, …) into
Claude Code as first-class subagents — so you can use Kimi Code as an **external
reviewer** for daily coding tasks (`/kimi-code-review`, `/kimi-opinion`) plus
structured review/planning/adversarial loops.

**Status: v1.3.0 — integration-ready, verified end-to-end on Windows
(sentinel-based completion + multi-provider model selection, now with a
per-call `[<model>]` bracket selector on every slash command).**
See [CHANGELOG.md](CHANGELOG.md) for the technical release log.

## Features

- **Daily-coding skills** (single-pass): `code-review` (`/kimi-code-review`),
  `second-opinion` (`/kimi-opinion`), `bridge` (`/kimi-run`).
- **Loop skills**: `review-loop` (iterative), `santa-loop` (adversarial
  dual-review, fail-closed), `planning-loop` (iterative plan refinement).
- Spawn Kimi Code headlessly via `kimi -p ... --output-format stream-json`
  (verified against `@moonshot-ai/kimi-code` **0.22.2**).
- **Multi-provider model selection**: every MCP tool takes an optional
  `model` parameter (a model alias from the CLI's own config, passed as
  `kimi -m`), so runs can be routed to any configured provider/model
  (Kimi, GLM, …). Model values are validated so they can never inject flags.
  On the slash-command surface, append the alias in square brackets —
  `/kimi-code-review src/foo.py [glm-4.6]` — to pick the model per call.
- Extensible agent registry: `kimi` (working adapter), `codex` (skeleton, raises
  `NotImplementedError` in v1.0 — present only to validate the abstraction).
- Single async execution path: adapters `await` one shared, depth-guarded
  runner, so the same code works in tests and inside the MCP event loop.
- **Windows-safe subprocess runner**: spawns the agent in a worker thread with
  `stdin=DEVNULL` + `CREATE_NO_WINDOW`, avoiding the ProactorEventLoop
  pipe-inheritance block that hung the MCP server.
- **Sentinel-based completion**: the runner returns as soon as Kimi emits its
  terminal `session.resume_hint` event and then kills the whole child process
  tree — required because `kimi -p` never exits when long-lived MCP servers
  are configured in the user's global `~/.kimi-code/mcp.json`, which used to
  turn every successful review into a timeout.
- Message protocol with recursion depth-guard (`KIMI_BRIDGE_DEPTH`, default 2).
- Read-only default policy, isolated worktrees (under the system temp dir),
  policy ceiling via `KIMI_MAX_POLICY`.
- Robust stream-json parsing: handles single-event, multi-event `tool_calls`,
  and plain-prose output; empty output fails safe (never reads as approval).
- MCP server exposing `run_agent`, `run_review_loop`, `run_santa_loop`, and
  `run_planning_loop`.

## Slash commands

| Command | Purpose |
|---|---|
| `/kimi-code-review <target> [<model>]` | Focused single-pass external code review (daily workhorse). |
| `/kimi-opinion "<question>" [--file <path>] [<model>]` | Quick external second opinion on a design decision. |
| `/kimi-run [agent] "<prompt>" [<model>]` | One-off prompt to a registered agent (default `kimi`), optionally on a specific model. |
| `/kimi-review <target> [--loop review\|santa] [--agent <name>] [<model>]` | Iterative or adversarial dual-review loop. |

`[<model>]` is an optional trailing bracket selector: write a model alias from
the agent CLI's own config in square brackets at the end of the command, e.g.
`/kimi-code-review src/foo.py [glm-4.6]`, to route that call to a specific
provider/model. `--model <alias>` works too. Omitted = the CLI's default model.
See [Model selection](#model-selection-multi-provider).

## Skills

| Skill | Type | Use |
|---|---|---|
| `code-review` | single-pass | One thorough review pass, prioritised findings. |
| `second-opinion` | single-pass | Decisive design/trade-off check. |
| `bridge` | single-pass | Lowest-overhead one-shot agent call. |
| `review-loop` | loop | Multi-round refinement against the same target. |
| `santa-loop` | loop | Fail-closed adversarial dual-review (two reviewers must agree). |
| `planning-loop` | loop | Iterative plan creation/refinement. |
| `contract-audit` | audit (single-pass) | Frozen API/data-model contract vs implementation — drift, missing pieces, fail-safe gaps. |
| `seam-design-review` | audit (single-pass) | Proposed module boundary/interface — coupling direction, deep-module quality, leaks. |
| `fail-safe-audit` | audit (single-pass) | Every failure/stale/fault path checked for safe-state behavior. |
| `test-gap-audit` | audit (single-pass) | Audits the *tests* for missing cases (edge, error, fail-safe, documented incidents). |

The four audit skills are role-specific lenses (architect / backend dev) that
complement the generic skills: each ships a structured brief the generic
skills do not. They have no slash command — call
`mcp__kimi-code-plugin-cc__run_agent` with the brief from the skill, and
escalate safety-critical sign-offs to `santa-loop`.

## Prerequisites

1. **Kimi Code CLI** installed and authenticated
   (`npm i -g @moonshot-ai/kimi-code`). Check with `kimi --version`.
2. **`uv`** on PATH (used by the MCP server to run the Python package).
3. **Claude Code** with the plugin system enabled.

## Install (development)

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Install in Claude Code

From inside this repo's directory in a Claude Code session:

```
/plugin marketplace add ./
/plugin install kimi-code-plugin-cc@kimi-code-cc
```

`kimi-code-cc` is the marketplace name declared in
`.claude-plugin/marketplace.json`. For a GitHub-based install, push the repo
publicly and use `/plugin marketplace add <user>/<repo>` instead. The canonical
repository is `https://github.com/ArchiDoxx/Kimi-code-Plugin-CC`, so:

```
/plugin marketplace add ArchiDoxx/Kimi-code-Plugin-CC
```

Verify with `/plugin list` (should show `kimi-code-plugin-cc`) and
`claude plugin validate .` from a shell.

## Usage — daily coding

```
/kimi-code-review src/myfile.py        # focused external code review
/kimi-code-review src/myfile.py [glm-4.6]  # same review on a specific model
/kimi-opinion "Should I use an ORM or raw SQL here?"  # quick second opinion
/kimi-run "Explain the bridge module"  # one-off prompt to Kimi
/kimi-review src/myfile.py --loop santa  # adversarial dual-review (fail-closed)
/kimi-review src/myfile.py --loop santa [zai-coding-plan/glm-5.2]  # …on GLM
```

## MCP server

The MCP server starts automatically once the plugin is installed. To run it
manually (for debugging):

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} kimi-code-plugin-mcp
```

The server exposes four tools:

- `run_agent(agent_name, prompt, approval_policy="read-only", model=None)` —
  one-shot run.
- `run_review_loop(agent_name, target, max_iterations=3, model=None)` —
  iterative review.
- `run_santa_loop(primary_agent, target, max_iterations=3, model=None)` —
  fail-closed adversarial dual-review (the model applies to both reviewers).
- `run_planning_loop(agent_name, prompt, max_iterations=3, model=None)` —
  iterative plan.

`approval_policy` is capped against the `KIMI_MAX_POLICY` environment variable.
For review/planning, pass file **contents** as the target/prompt: the agent runs
in an isolated worktree and cannot open arbitrary host paths.

### Model selection (multi-provider)

`model` is a model alias from the **agent CLI's own config** — for kimi, an
alias defined in `~/.kimi-code/config.toml`, e.g. `zai-coding-plan/glm-5.2` or
`moonshot-ai/kimi-k2.6`. It is passed through as `kimi -m <alias>`, so any
provider/model you configure in the CLI works immediately; the plugin holds no
model list of its own. Omitted, the CLI's `default_model` applies. Values are
validated against `[A-Za-z0-9][A-Za-z0-9._:/-]*`, so a model value can never
smuggle a CLI flag into the argv.

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="Review this diff …",
  model="zai-coding-plan/glm-5.2",
)
```

On the slash-command surface the same routing is available as a trailing
bracket selector — `/kimi-code-review src/foo.py [zai-coding-plan/glm-5.2]` —
on all four commands. Loose names like `[GLM 4.6]` are normalized to the
alias form (`glm-4.6`) before the call; the adapter rejects anything that
does not match the alias charset.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `KIMI_MAX_POLICY` | `read-only` | Hard ceiling on the approval policy; model-driven escalation is blocked above this. |
| `KIMI_BRIDGE_DEPTH` | `2` | Maximum recursion depth for nested agent spawns. |
| `KIMI_WORKTREE_BASE` | system temp | Base directory for isolated agent worktrees. |

## Live smoke-test steps

1. Ensure `kimi` CLI is installed and authenticated (`kimi --version`).
2. Install the plugin in Claude Code (see above).
3. Verify the plugin loaded: `/plugin list` should show `kimi-code-plugin-cc`.
4. Run a simple round-trip:
   ```
   /kimi-run kimi "Return the word 'pong'"
   ```
5. Verify the response contains `pong` and no `--yolo` / `--auto` flags were
   used (they are structurally never injected).
6. Run a focused code review:
   ```
   /kimi-code-review src/kimi_code_plugin_cc/security/policy.py
   ```
7. Run an adversarial review and confirm it returns a verdict and respects the
   fail-closed rule:
   ```
   /kimi-review src/kimi_code_plugin_cc/security/policy.py --loop santa
   ```

## Running tests

```bash
uv run pytest
uv run pytest --cov=kimi_code_plugin_cc --cov-report=term-missing
```

**After a Kimi CLI update:** the plugin is deliberately not pinned to a CLI
version — it depends only on a small public flag surface (`-p`,
`--output-format stream-json`, `-m`). `tests/test_cli_contract.py` pins that
surface against the real `kimi --help` output (it skips automatically when the
CLI is absent). The opt-in live tests exercise the full transport:

```bash
uv run pytest tests/test_cli_contract.py   # cheap contract check
uv run pytest -m live                      # full live round-trip (spawns kimi)
```

## License

MIT
