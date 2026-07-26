# Implementation plan: working `codex` adapter (second real agent behind the bridge)

Status: ready for implementation. Target version: next minor after 1.5.0
(entry under `## [Unreleased]`; no version bump in this task).
Author: planning pass 2026-07-26. Flag surface verified LIVE against the
installed `codex-cli 0.145.0` (`codex exec --help`) — re-verify against the
installed CLI before pinning anything, exactly as `CONTRIBUTING.md` demands.
Reviewer: the planning agent reviews the finished PR against the acceptance
criteria at the bottom.

## Why

The registry claims "extensible bridge for headless CLI agents", but `codex`
is a skeleton raising `NotImplementedError` — the abstraction has never been
proven with a second adapter. A working codex adapter (a) makes the framing
honest, (b) unlocks a fully external heterogeneous santa loop
(`primary=kimi, adversary=codex` — two different vendors, no host reviewer
needed), and (c) exercises every seam the plugin says is agent-agnostic:
registry, runner, policy mapping, error contract, capability probing.

## Non-goals

- No new MCP tools, slash commands, or skills — `agent_name="codex"` flows
  through the existing surface (`run_agent`, loops, `--agent` flag).
- No auth handling: the adapter uses the user's existing local Codex login
  (`~/.codex/auth.json` via `CODEX_HOME`); a missing login surfaces as the
  CLI's own error through the normal `agent_failed` path.
- No interactive mode, no `codex exec resume/review` subcommands.
- No transcript-recorder changes (loops record adapter-agnostically already).

## Hard invariants (review will gate on these)

1. **Fail-closed inherited, not reimplemented.** Empty output, unparseable
   output, timeout, non-zero exit all resolve through the existing paths
   (`errors.py` taxonomy, loop verdict fallbacks). No new failure path may
   read as approval.
2. **Structural ban on auto-approve.** The codex equivalents of `--yolo` are
   `--dangerously-bypass-approvals-and-sandbox` and
   `--dangerously-bypass-hook-trust` (verified in 0.145.0), plus the sandbox
   value `danger-full-access`. All three are NEVER emitted by the adapter —
   same mechanism as `NEVER_FLAGS` in `agent_registry/kimi.py`, tested.
3. **Policy mapping is exact and capped.** Plugin policy `read-only` ->
   `--sandbox read-only` (default); `workspace-write` -> `--sandbox
   workspace-write` only via the existing escalation rules
   (`KIMI_MAX_POLICY`, human approval above the ceiling).
   `danger-full-access` is not reachable through any input.
4. **One execution path.** The adapter awaits the shared runner
   (`bridge/runner.py`) like kimi does: worktree isolation, depth guard, env
   allowlist, prompt-size guard (`_assert_prompt_fits` pattern) all apply.
   No parallel spawn logic.
5. **Nothing is pinned from memory.** Every flag beyond the base surface is
   gated through `agent_registry/capabilities.py` probing; the base surface
   itself is pinned by a contract test against the real `codex exec --help`.

## Design

### Command shape (base surface, verified 0.145.0)

```
codex exec --sandbox read-only --json -o <run_dir>/last-message.txt [-m <model>] <prompt>
```

- `codex exec` is batch: it exits on its own. Completion is the runner's
  natural-exit path — **no sentinel, no process-tree kill needed**. The
  existing timeout stays as pure backstop. Document this difference from
  kimi in the adapter docstring.
- **Payload source of truth:** the `-o/--output-last-message` file, read
  with explicit UTF-8 after exit. Missing or empty file -> `agent_failed`
  (fail-closed), regardless of exit code. The `--json` JSONL stream on
  stdout is captured for diagnostics but parsing it is NOT load-bearing.
- Prompt travels as argv (existing size guard applies). Codex also accepts
  stdin prompts — note it in a comment as the future fix for the argv cap,
  but do not build it now (the Windows runner pins `stdin=DEVNULL`).
- Model: `-m <alias>`, validated with the same alias regex as kimi (reuse,
  do not duplicate the pattern).

### Isolation (capability-gated, mirrors kimi's skills isolation)

- `--ephemeral` (no session files persisted) and `--ignore-user-config`
  (user config ignored, auth still honored) make reviews reproducible and
  keep the user's `~/.codex` session store clean. Both verified in 0.145.0;
  both gated through `supports_flag(prefix, ...)` so older CLIs keep the
  plain command shape. Opt-out env var analogous to `KIMI_ISOLATE_SKILLS`.
- `--skip-git-repo-check` only if the live test shows the temp worktree
  needs it — decide from evidence, not preemptively; gate it if added.

### Registry, errors, env

- `agent_registry/codex.py`: replace the skeleton, keep the registered name
  `codex`. Constructor mirrors `KimiCodeAdapter` (model default, isolation
  toggle). Resolution: `shutil.which("codex")`; the CLI is a native binary,
  so the Windows `.CMD` de-shim from kimi does NOT apply by default — but on
  npm installs a `codex.cmd` wrapper may exist; reuse the existing de-shim
  helper when the resolved path is a `.CMD` wrapper, else use the binary
  directly.
- `errors.py` needs no new kinds. `not_installed` hint for codex:
  `npm install -g @openai/codex` (verify the hint against the CLI's own
  install docs at implementation time; if unverifiable, name the binary and
  say "install Codex CLI" without a channel).
- Env allowlist: forward `OPENAI_`-prefixed variables and `CODEX_HOME` to
  the child. Verify via the live test that exec works under the runner's
  minimal environment.

### Doctor (small, explicit)

Add a codex section to `doctor`: absent CLI -> `[warn]` (kimi remains the
primary agent; codex absence must not fail preflight); present -> report
version and check the base flag surface (`exec`, `--sandbox`, `--json`,
`--output-last-message`, `-m`). Missing base flag on an installed codex ->
`[fail]`.

## Tests (write first)

Contract (`tests/test_cli_contract.py`, new class, skips without codex):

1. `codex exec --help` documents the pinned base surface: `--sandbox` with
   `read-only`/`workspace-write`, `--json`, `-o, --output-last-message`,
   `-m, --model`.
2. The banned flags still exist under their pinned names (a rename must
   break the build so invariant 2 stays enforceable).

Adapter unit tests (`tests/test_agent_registry.py`, fake runner as for kimi):

3. Command shape: base flags present, prompt last, model appended when set.
4. Policy mapping: read-only default; workspace-write only when escalation
   rules allow; `danger-full-access` unreachable (attempt -> error).
5. Banned flags never appear in argv for any input (parametrized over
   policies/models/contexts).
6. Payload comes from the last-message file; missing file -> classified
   failure even on exit 0; empty file -> classified failure.
7. Non-zero exit with stderr -> `agent_failed` carrying stderr context.
8. Isolation flags appended only when the probe reports support; opt-out
   env respected.
9. Model alias validation rejects flag-injection shapes (reuse kimi cases).
10. Prompt-size guard applies to codex like to kimi.

Loop integration (existing stub patterns):

11. `run_review_loop(agent_name="codex")` with a stubbed adapter returns a
    verdict — proves the registry path end to end.

Doctor:

12. codex absent -> `[warn]`, `doctor` still exits 0 (given kimi is fine).
13. codex present (mocked probe) with missing base flag -> `[fail]`.

Live (`-m live`, skipped without CLI + login):

14. One real `codex exec` round trip through the adapter: prompt in, text
    out, worktree used, no session files left behind when `--ephemeral` is
    active.

## Docs to update

- `README.md`: registry bullet ("codex: working since vX"), one usage line
  (`/kimi-run codex "..."` and `--agent codex` on `/kimi-review`), env note.
- `CHANGELOG.md` under `## [Unreleased]`.
- `CLAUDE.md`: architecture line for the codex adapter (one line).
- `SECURITY.md`: extend the auto-approve ban list with the codex flag names.

## Acceptance criteria

- `uv run ruff check .`, `uv run ruff format --check .` clean;
  `uv run pytest -q` fully green; coverage >= 90% overall (gate: 80).
- Tests 1-13 pass in CI (14 is live-only). Tests 4, 5, 6 are the review
  focus: policy cap, banned flags, fail-closed payload.
- No diff in `loops/`, `mcp_server.py`, `transcript*.py`, CI workflows,
  version files. `errors.py` only if a hint string is added.
- New/changed modules under 400 lines; functions under 50; no emojis.

## Process expectations for the implementing agent

Branch `feat/codex-adapter` off `main`, test-first, conventional commits,
PR via `gh pr create` referencing this plan — do not merge, external review
first. Verify every pinned flag against the INSTALLED CLI before writing it
down; if the local CLI disagrees with this plan, the CLI wins — note the
deviation in the PR body. For destructive or security-relevant paths, write
adversarial tests (inputs trying to smuggle banned flags or policies), not
only happy paths. Stop and leave a note instead of guessing if an invariant
cannot be met.
