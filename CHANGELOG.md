# Changelog

Technical release log for `kimi-code-plugin-cc`. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver and
match `.claude-plugin/plugin.json` / `pyproject.toml`.

## [Unreleased]

### Added

- **Working `codex` adapter** (`agent_registry/codex.py`): the OpenAI Codex CLI
  is now a real second agent behind the bridge, not a skeleton. Use it through
  the existing surface — `agent_name="codex"`, `/kimi-run codex "..."`,
  `--agent codex`. `santa_loop(primary_agent="kimi",
  adversary_agent="codex")` now gives a cross-vendor adversarial review with
  no host reviewer; note that the `run_santa_loop` MCP tool still does not
  expose `adversary_agent`, so that combination is reachable from the Python
  API only. Command shape verified live against `codex-cli 0.145.0`:
  `codex exec --sandbox <mode> --json -o <file> [gated flags] [-m <model>] --
  <prompt>`.
  - `codex exec` is batch and exits on its own, so unlike kimi it needs no
    completion sentinel and no process-tree kill; the timeout is a pure
    backstop.
  - The answer is read from the `-o/--output-last-message` file, written to a
    temp directory **outside** the agent's workspace. The `--json` stream is
    kept for diagnostics only, so an upstream event-schema change cannot
    corrupt a verdict. A missing or empty file is a failure even on exit 0 —
    an empty answer can never read as an approval.
  - Policy mapping is exact and capped: `read-only` -> `--sandbox read-only`,
    `accept-edits` -> `--sandbox workspace-write` but only up to
    `KIMI_MAX_POLICY`. `explicit` is refused (nothing can approve per-action
    in a non-interactive run), and `danger-full-access` is unreachable because
    it is not a plugin policy name.
  - The structural auto-approve ban now covers codex:
    `--dangerously-bypass-approvals-and-sandbox`,
    `--dangerously-bypass-hook-trust` and `danger-full-access` are never
    emitted — asserted for every policy/model/capability combination, and the
    names are pinned against the real CLI so an upstream rename breaks the
    build instead of silently unenforcing the ban.
  - The prompt is passed after `--`, so a prompt starting with a hyphen stays a
    positional value instead of being parsed as an unknown flag.
  - `--skip-git-repo-check` (required: the isolated worktree is not a git
    repository), plus `--ephemeral` and `--ignore-user-config` for
    reproducibility, all capability-gated. Opt out of the latter two with
    `KIMI_CODEX_ISOLATE_SESSION=0` if you rely on `config.toml` aliases.
- `doctor` reports codex separately: absent is a `[warn]` (kimi remains the
  primary agent, so preflight still passes), while an installed codex whose
  flag surface has drifted is a `[FAIL]`.
- `agent_registry/common.py`: guards shared by all adapters (prompt-size cap,
  model-alias validation, Windows `.cmd` de-shimming, PATH resolution), moved
  out of the kimi adapter so a second adapter reuses them instead of copying
  them — a duplicated security guard is a defect waiting to drift.
- `errors.AgentNotInstalledError` carries the *agent's own* install channel, so
  a missing codex no longer tells you to install kimi.

### Changed

- The child-process environment allowlist also forwards `OPENAI_*` and
  `CODEX_HOME`, which codex needs in order to authenticate. `OPENAI_API_KEY` is
  covered by the new prefix and was dropped from the exact list.
- `AdapterNotImplementedError` moved from `agent_registry/codex.py` to
  `agent_registry/base.py`: it belongs to the adapter contract, and codex is no
  longer a skeleton.

- **Loop run transcripts** (`transcript.py`): every review/santa/planning run
  persists a local transcript under
  `~/.kimi-code-plugin-cc/transcripts/<run_id>/` (a `run.json` summary plus
  one `round-NN-<role>.md` per exchange), so a review can be audited after
  the fact. The recorder is fail-open (a disk error can never crash or alter
  a loop, and a crashed run keeps its partial transcript) and bounded
  (`KIMI_TRANSCRIPT_KEEP`, default 50 newest runs kept, older pruned at the
  start of a run). Disable entirely with `KIMI_TRANSCRIPTS=0`; relocate with
  `KIMI_TRANSCRIPT_DIR`. Loop results and the loop MCP tools expose the run
  directory as `transcript_dir` (`null` when recording is off).

- **Transcript viewer** (`transcript_view.py`, `kimi-code-plugin transcripts`):
  the read half of the transcript subsystem — `transcripts list [--limit N]`
  for the newest runs (id, loop, final verdict, rounds, start time),
  `transcripts show <run-id-or-unique-prefix>` for a run summary plus its
  rounds, and `transcripts show <id> --round N` for the recorded round(s)
  verbatim. Without it the audit trail existed but was only readable by
  digging through `~/.kimi-code-plugin-cc/transcripts/` by hand. Strictly
  read-only (no lock file, no cache, no touch), so it is safe to run against a
  transcript a loop is still writing. The failure modes it is built for are
  the runs worth auditing: a corrupt `run.json` lists as `(unreadable)` and
  `show` falls back to the round files on disk, a round file `run.json`
  promises but disk lacks becomes a marked row, an unfinalized run lists as
  `(incomplete)` — none of it a traceback. Output survives a non-UTF-8 console
  (a single unicode arrow in a review used to be enough for
  `UnicodeEncodeError` to eat the whole page). Exit codes: `0` for every
  success path including an empty listing, `1` for an unknown or ambiguous run
  id and for a missing round.

- **Daily CLI canary** (`.github/workflows/canary.yml`): installs the *latest*
  `@moonshot-ai/kimi-code` on Linux and Windows every day, verifies the pinned
  flag surface (`tests/test_cli_contract.py`) and runs `doctor`. Upstream CLI
  drift becomes a failure notification within a day instead of a broken review
  on a user's machine. A hard `kimi --version` step prevents the contract test
  from skipping itself silently. Contract tier only — no credentials; a live
  round-trip tier can be added later via an API-key secret.

## [1.4.0] — 2026-07-25

Loop-correctness and operability release. The loops were sound in round 1 and
degraded afterwards; the MCP surface had no error contract at all. Verified
against `@moonshot-ai/kimi-code` **0.29.1** (previous releases pinned 0.22.2).

### Fixed

- **The verdict contract was stated only in round 1.** `_build_refinement_prompt`
  (review), `_build_revision_prompt` and `_adversarial_prompt` (santa) each built
  their prompt independently and dropped the `VERDICT:` instruction, so from
  iteration 2 onward — and for *every* adversarial second review — the verdict
  came from the fuzzy free-text fallback instead of the machine-readable line.
  All rounds now build through the new `loops/prompts.py`, so one round cannot
  disagree with another about the output contract.
- **Ambiguous verdicts could resolve to approval.** `extract_verdict` used
  `re.search`, taking the *first* `VERDICT:` line. The santa revision and
  adversarial prompts quote the other reviewer's full text, so a reply can
  legitimately contain two verdict lines — and the quoted one could win. All
  structured lines are now collected: unanimous ones are honoured, conflicting
  ones resolve to `needs_discussion`. Ambiguity is disagreement, never approval.
- **`santa-loop` was fail-crashed, not fail-closed.** The three loop MCP tools
  had no exception handling whatsoever. A missing CLI, a timeout, a non-zero
  exit or an unknown agent name escaped as a raw protocol exception with no
  verdict attached, so a caller could read "the tool errored" as "no signal"
  rather than "not approved". Every tool now returns a classified error that
  still carries the fail-closed verdict (`red` / `needs_discussion`).
- **Oversized prompts failed opaquely.** The prompt travels as an argv element
  and Windows caps a command line at 32767 characters, but callers are told to
  paste file *contents* (the agent runs in an empty worktree). Prompts are now
  checked before the spawn and rejected with an actionable message. Configurable
  via `KIMI_MAX_PROMPT_CHARS` (default 30000); a broken override falls back to
  the default rather than silently disabling the guard.
- **`cli.py` was unreachable.** It defined a `main()` that nothing imported and
  that `pyproject.toml` never registered — 0% coverage, dead on arrival.

### Added

- **`kimi-code-plugin doctor`** — preflight diagnostics. Checks that the agent
  CLI is on PATH, reports its version, verifies the pinned flag surface
  (`-p`, `--output-format`, `-m`), reports whether skills isolation is active,
  and prints the effective policy ceiling, depth guard, prompt limit and
  worktree base. Exits non-zero on failure so it works in setup scripts and CI.
  Turns the most common first-run failures into one command instead of a
  confusing error in the middle of a review.
- **Skills isolation for reproducible reviews** (`--skills-dir` pointed at an
  empty directory inside the run's worktree). Without it the agent
  auto-discovers the host's global and project skills, so the same review can
  produce different output on two machines. Gated behind runtime capability
  detection (`agent_registry/capabilities.py`) because the plugin deliberately
  does not pin the CLI version — an older CLI that lacks the flag keeps the
  previous command shape rather than failing on an unknown flag. Opt out with
  `KIMI_ISOLATE_SKILLS=0` or `KimiCodeAdapter(isolate_skills=False)`.
- **`errors.py`** — one error contract for the MCP surface: stable `ErrorKind`
  values (`not_installed`, `unknown_agent`, `not_implemented`,
  `policy_refused`, `invalid_input`, `timeout`, `agent_failed`, `internal`) with
  actionable hints. `run_agent` errors now carry an unmistakable prefix, since
  that tool returns the agent's text verbatim on success. The `codex` skeleton
  is reported as `not_implemented` instead of raising through the boundary.
- **A standalone preamble on every loop prompt**, suppressing project-context
  loading and persona adoption from the host's global agent instructions.
  Defensive hardening: an A/B probe against 0.29.1 produced clean, on-task
  output with *and* without it, so this is insurance, not a fix for a currently
  reproducing bug.
- **CI** (`.github/workflows/ci.yml`): ruff + format check + pytest with
  coverage on Linux and Windows across Python 3.11-3.13, plus a job that fails
  when the `plugin.json` and `pyproject.toml` versions drift apart.
- `LICENSE` (the repo claimed MIT in three places without shipping the file),
  `SECURITY.md`, `CONTRIBUTING.md`, and a PEP 561 `py.typed` marker.

### Changed

- `run_agent`'s error prefix changed from `error: ...` to
  `kimi-code-plugin-cc error [<kind>]: ...`. Success output is byte-identical.
- An unknown `agent_name` no longer raises; it returns a classified error.

### Verified

- 188 tests green (54 new), ruff clean, coverage 94%.
- `--skills-dir` confirmed live against 0.29.1 (empty dir, clean exit).
- `doctor` confirmed live: resolves the de-shimmed node entry point, reports
  0.29.1, all required flags present, isolation active.

## [1.3.1] — 2026-07-22

### Fixed

- Renamed `seam-design-review` to `team-design-review` and corrected all
  occurrences of "seam" to "team" across the skill, `contract-audit`, README,
  and CHANGELOG. This was a persistent typo from the upstream template that
  used "Team-sync" and was accidentally written as "Seam-sync".

## [1.3.0] — 2026-07-07

Bracket model selector on the slash-command surface; four role-specific
audit skills.

### Added

- Four **audit skills** (single-pass, no new MCP tools, no slash commands):
  `contract-audit` (frozen contract vs implementation drift),
  `team-design-review` (proposed module boundary/interface),
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
