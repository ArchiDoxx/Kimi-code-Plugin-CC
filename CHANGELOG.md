# Changelog

Technical release log for `kimi-code-plugin-cc`. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver and
match `.claude-plugin/plugin.json` / `pyproject.toml`.

## [1.3.0] — 2026-07-07

Bracket model selector on the slash-command surface; four role-specific
audit skills.

### Added

- Four **audit skills** (single-pass, no new MCP tools, no slash commands):
  `contract-audit` (frozen contract vs implementation drift),
  `seam-design-review` (proposed module boundary/interface),
  `fail-safe-audit` (every failure/stale/fault path vs safe-state invariant),
  `test-gap-audit` (missing test cases — edge, error, fail-safe, documented
  incidents). Role-specific briefs that complement the six generic skills;
  safety-critical sign-offs escalate to `santa-loop`. Built in a parallel
  worktree, integrated flattened to `skills/<name>/` (nested skill dirs are
  not discovered by the plugin loader) and stripped of project-specific
  references.

- Trailing `[<model-alias>]` selector on all four slash commands
  (`/kimi-code-review`, `/kimi-opinion`, `/kimi-run`, `/kimi-review`):
  `/kimi-code-review src/foo.py [glm-4.6]` routes that call to the `glm-4.6`
  alias from the agent CLI's own config. Parsed at the command layer and
  passed as the existing `model` parameter (v1.2.0) of the MCP tools — no
  Python changes. `--model <alias>` remains equivalent on every command.
- Loose names (`[GLM 4.6]`) are normalized to alias form (`glm-4.6`) before
  the call and the substitution is stated; final validation stays in the
  adapter (`[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}`), so the bracket syntax
  cannot bypass the flag-injection guard.
- Brackets that are part of inline code (e.g. `x[i]`) are explicitly excluded
  from selector parsing; for `/kimi-review --loop santa` the selector applies
  to both external reviewers, never to the host (Claude) review.
- Skills (`code-review`, `second-opinion`, `bridge`, `review-loop`,
  `santa-loop`, `planning-loop`) and README document the new syntax.

### Changed

- Markdown-only release: commands, skills, README, version metadata. The
  Python package is unchanged apart from the version bump.

## [1.2.0] — 2026-07-07

Multi-provider model selection.

### Added

- Optional `model` parameter on all four MCP tools (`run_agent`,
  `run_review_loop`, `run_santa_loop`, `run_planning_loop`). The value is a
  model alias from the agent CLI's own config (for kimi:
  `~/.kimi-code/config.toml`, e.g. `zai-coding-plan/glm-5.2`) and is passed
  through loops → adapter context → `kimi -m <alias>`. The plugin holds no
  model list; new providers configured in the CLI work immediately.
- `KimiCodeAdapter(model=...)` constructor default; a per-call `model` context
  key overrides it.
- Model-alias validation (`[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}`): a value can
  never begin with `-` or contain whitespace/quotes, so flag injection through
  the model parameter is structurally impossible (same posture as the
  `NEVER_FLAGS` guard against `--yolo`/`--auto`).
- `run_agent` returns a structured `error: ...` string for invalid model
  aliases (ValueError) instead of an opaque stack trace at the MCP boundary.
- The santa loop forwards the model to **both** the primary reviewer and the
  external adversary; the host-reviewer path is unaffected.
- New `tests/test_cli_contract.py`: pins the CLI flag surface the adapter
  depends on (`-p`, `--output-format stream-json`, `-m`) against the real
  `kimi --help` output, so daily CLI updates that break the interface are
  caught by a test run instead of by the first broken review. Skips when the
  CLI is not on PATH.

### Verified

- E2E on Windows: explicit non-default alias `zai-coding-plan/glm-5.1`
  answered in 18 s; an alias for a provider without credentials surfaced the
  CLI's own auth error cleanly (proves both routing and the error path).
- 134 tests green (19 new), ruff clean.

## [1.1.0] — 2026-07-07

Fixes the `/kimi-review` timeout: every successful run used to burn the full
timeout and discard the finished answer as `possible auth hang`.

### Fixed

- **Root cause** (verified against `@moonshot-ai/kimi-code` 0.22.2): `kimi -p`
  prints its complete answer and then **never exits** when long-lived MCP
  servers are configured in the user's global `~/.kimi-code/mcp.json`. The
  runner waited on process exit via `subprocess.run(timeout=...)`.
- **Sentinel-based completion**: the runner now streams stdout line by line
  (`Popen` + reader threads) and completes the run as soon as Kimi's terminal
  `{"role":"meta","type":"session.resume_hint"}` event appears. The child gets
  a 2 s grace period to exit on its own, then its **whole process tree** is
  killed (`taskkill /T /F` on Windows, `killpg` on POSIX) so spawned MCP
  servers cannot accumulate as orphans. `RunResult` gains `early_exit`; the
  adapter no longer treats the reaped child's exit code as failure.
- Graceful degradation: if a future CLI stops emitting the sentinel, the
  runner falls back to the previous semantics (natural exit returns normally,
  deadline raises `TimeoutError` with partial output) — no hard break.
- Default timeout raised 120 s → 600 s; with the sentinel it is a pure
  backstop, not the expected completion path.
- Env allowlist: `MOONSHOT_` prefix forwarded (API-key deployments).
- Docs drift: verified CLI is `@moonshot-ai/kimi-code` 0.22.2 (README
  previously named `@kimi-code/kimi` 0.20.1).

### Verified

- E2E on Windows against the real CLI + global MCP config (the scenario that
  always hung): answer in 16 s, `node.exe` process count unchanged (no
  orphans). 115 tests green (9 new), ruff clean.

## [1.0.0] — 2026-06-28

Initial integration-ready release.

- Bridge for headless CLI agents (Kimi Code working, Codex skeleton) with a
  single async, depth-guarded execution path.
- Windows fixes: thread-backed subprocess with `stdin=DEVNULL` +
  `CREATE_NO_WINDOW` (Proactor pipe-inheritance block); `.CMD` shim de-shim to
  `node main.mjs` so multi-line prompts survive `cmd.exe`.
- Review / santa (adversarial, fail-closed) / planning loops; MCP server with
  four tools; skills and slash commands.
- Security posture: read-only default policy, `KIMI_MAX_POLICY` ceiling,
  isolated worktrees, structural ban on auto-approve flags, env allowlist for
  child processes.
