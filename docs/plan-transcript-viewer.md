# Implementation plan: transcript viewer CLI (`kimi-code-plugin transcripts`)

Status: ready for implementation. Target version: next minor (stays under
`## [Unreleased]` in the changelog — no version bump in this task).
Author: planning pass 2026-07-26, verified against the code on `main`
(`f938b6b`). Reviewer: the planning agent reviews the finished PR against
the acceptance criteria at the bottom.

## Why

v1.5.0-to-be writes a transcript for every loop run (`transcript.py`,
merged in PR #8), but reading one currently means digging through
`~/.kimi-code-plugin-cc/transcripts/` by hand. A read-only viewer turns the
persistence feature into a usable audit trail: list recent runs, open one,
read what each reviewer actually said in a given round. This is the natural
companion to `doctor` on the CLI surface.

## Non-goals

- No HTML/browser rendering, no paging, no colors.
- No deletion, pruning, or any other write operation - the viewer is
  strictly read-only.
- No MCP tool for browsing (CLI only).
- No changes to what the recorder writes.

## Hard invariants (review will gate on these)

1. **Strictly read-only.** `list`/`show` must never create, modify, or
   delete anything under the transcript base directory. No lock files, no
   caches, no "touch".
2. **Garbage in, report out - never a traceback.** A corrupt or truncated
   `run.json` (crashed runs are the *expected* customers of this tool), a
   missing round file, or foreign content in the base dir must produce a
   readable message or a marked table row, never an unhandled exception.
3. **Windows console safety.** Transcripts contain arbitrary UTF-8 (the
   documented project trap: `→` under a cp1252 console raises
   `UnicodeEncodeError` on print). Output must survive a non-UTF-8 stdout -
   reconfigure stdout to `errors="replace"` where supported, or encode
   defensively. A round containing `→` must print without crashing.
4. **Only run-shaped directories are considered.** Reuse `_RUN_ID_RE` and
   `_base_dir` from `transcript.py` via intra-package import (same
   distribution, acceptable) - do NOT duplicate the pattern or the base-dir
   resolution, and do NOT widen their visibility.
5. **Stdlib only.** No new dependencies.

## Design

### New module: `src/kimi_code_plugin_cc/transcript_view.py`

Pure, testable core + thin printing shell. Suggested surface (naming free,
behavior fixed):

```python
def list_runs(limit: int = 20) -> list[RunSummary]: ...
def resolve_run_id(prefix: str) -> Path: ...      # unique-prefix resolution
def render_run_list(runs: list[RunSummary]) -> str: ...
def render_run(run_dir: Path) -> str: ...          # summary + rounds table
def render_round(run_dir: Path, index: int) -> str: ...  # raw round file(s)
```

`RunSummary` (dataclass or similar): `run_id`, `loop`, `started`,
`final` (verdict/status string, `"(incomplete)"` when the run has no
`final` key, `"(unreadable)"` when `run.json` cannot be parsed), and the
round count. Sorting: newest first (run ids are lexically chronological).

### CLI wiring (`cli.py`)

Extend the existing argparse setup (see `main()` - `mcp` and `doctor` are
the pattern to follow):

```
kimi-code-plugin transcripts list [--limit N]
kimi-code-plugin transcripts show <run-id-or-unique-prefix>
kimi-code-plugin transcripts show <run-id-or-unique-prefix> --round N
```

Behavior contract:

- `list` on an empty or not-yet-existing base dir: friendly one-liner,
  exit 0 (nothing recorded is not an error).
- `list` shows newest first, at most `--limit` rows (default 20), one row
  per run: run id, loop, final, rounds, started.
- `show <prefix>`: unique prefix of a run id is accepted; ambiguous prefix
  exits 1 and lists the candidates; unknown id exits 1 with an actionable
  message naming the base dir.
- `show` without `--round`: run summary (loop, agents, model, started,
  finished, final) plus a table of recorded rounds (index, role, agent,
  verdict, duration, file).
- `show --round N`: prints the raw content of every round file with that
  index (santa has two roles per index). Missing round: exit 1 with a
  message, no traceback.
- `transcripts` without a subcommand prints the transcripts help and
  exits non-zero (argparse default behavior is acceptable - pin whatever
  it does in a test).
- Exit codes: 0 for every success path (including empty list), 1 for
  user-facing errors (unknown/ambiguous id, missing round).

## Tests (write first; new `tests/test_transcript_view.py`, plus CLI-level tests through `cli.main([...])` with `capsys`)

Build fixtures by writing run dirs directly into a `tmp_path` base
(monkeypatch `KIMI_TRANSCRIPT_DIR`), or by driving the real
`TranscriptRecorder` - both are fine.

1. `list` on a missing/empty base dir: friendly message, exit 0.
2. `list` sorts newest first and respects `--limit`.
3. A run whose `run.json` lacks `final` is listed as `(incomplete)`.
4. A run with corrupt `run.json` (garbage bytes) is listed as
   `(unreadable)` - no exception.
5. Foreign directories and files in the base dir are ignored by `list`
   (reuses the run-id filter).
6. `show` renders the summary fields and one row per recorded round.
7. `show` resolves a unique run-id prefix.
8. An ambiguous prefix exits 1 and names the candidates.
9. An unknown run id exits 1 with a message naming the base dir.
10. `show --round N` prints the raw content of both santa round files for
    index N.
11. `show --round` for a round that does not exist exits 1 without a
    traceback.
12. A round containing `→` prints without `UnicodeEncodeError` when stdout
    is cp1252 (simulate with an `io.TextIOWrapper(..., encoding="cp1252")`
    or equivalent).
13. Read-only guarantee: after `list` + `show` + `show --round`, the set of
    files under the base dir and their mtimes are unchanged.
14. Exit codes: parametrized check of the 0 and 1 paths above.
15. `transcripts` without a subcommand: pinned help behavior (output
    mentions `list` and `show`; exit code pinned to whatever argparse
    produces).

## Docs to update

- `README.md`: short "Reading transcripts" note next to the existing
  transcript feature bullet (two or three commands, one sample `list` row).
- `CHANGELOG.md`: entry under `## [Unreleased]`. No version bump.
- `CLAUDE.md`: extend the `transcript.py` architecture line with the viewer
  module (one line).

## Acceptance criteria

- `uv run ruff check .` and `uv run ruff format --check .` clean.
- `uv run pytest -q` fully green; coverage stays >= 90% overall (gate: 80).
- All 15 test cases above exist and pass; tests 12 (console encoding) and
  13 (read-only) are the review focus.
- No diff in `transcript.py`, `loops/`, `mcp_server.py`, `errors.py`,
  `doctor.py`, CI workflows, or version files.
- New module stays under 400 lines; functions under 50; no emojis; ASCII
  hyphens in code and docs.

## Process expectations for the implementing agent

Work on a feature branch `feat/transcript-viewer` off `main`, test-first,
conventional commits (`feat: ...`, `test: ...`, `docs: ...`). When done and
locally green, push the branch and open a PR against `main` with
`gh pr create`, referencing this plan in the body. Do not merge - the PR
gets an external review first. Follow `CONTRIBUTING.md`. If the code
contradicts this plan anywhere, the code wins - note the deviation in the
PR body instead of forcing the plan through. Do not touch `.kimi-code/`,
`.work/`, or version numbers. Stop and leave a note instead of guessing if
an invariant cannot be met.
