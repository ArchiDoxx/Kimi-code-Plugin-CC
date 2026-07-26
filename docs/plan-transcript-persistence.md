# Implementation plan: transcript persistence for loop runs

Status: ready for implementation. Target version: next minor (1.5.0).
Author: planning pass 2026-07-26, verified against the code on `main`
(`f8beba9`). Reviewer: the planning agent reviews the finished diff against
the acceptance criteria at the bottom; external review via
`/kimi-code-review` before merge.

## Why

The loops (`review`, `santa`, `planning`) are the product's core, but they are
black boxes: when a santa run ends `red` after 20 minutes, the user cannot see
what either reviewer actually said in round 2. Raw per-round transcripts on
disk turn "the loop decided X" into something a user can audit. For a review
tool, traceability is a product feature, not a debug luxury.

The most valuable transcript is the one from a run that **crashed** — so
rounds must be written to disk as they complete, not at the end.

## Non-goals

- No viewer/CLI subcommand, no HTML rendering (later).
- No redaction, no upload, no telemetry. Transcripts stay local.
- No retention policy beyond simple keep-newest-N pruning.
- No transcript for single-shot `run_agent` (loops only).

## Hard invariants (review will gate on these)

1. **Persistence must never influence a verdict or crash a loop.** Every
   disk operation is best-effort: catch `OSError`/`UnicodeError`, log one
   warning via `logging`, disable the recorder for the rest of the run,
   continue. A loop with a broken recorder must return byte-identical
   results (except `transcript_dir`) to a loop with recording disabled.
2. **No behavioral change to prompts or parsing.** `loops/prompts.py` and
   `extract_verdict` are untouched.
3. **Stdlib only.** No new dependencies (`json`, `pathlib`, `datetime`,
   `secrets`, `shutil`, `logging`, `time`).
4. **Windows-safe.** Every file write passes `encoding="utf-8"` explicitly
   (cp1252 is the documented trap on this project — a review containing
   `→` must not crash or garble). Short file names; no path segments
   from user input.
5. **Fail-safe env parsing**, same pattern as `_max_prompt_chars` in
   `agent_registry/kimi.py`: a garbage override falls back to the default,
   never raises, never silently disables more than intended.

## Design

### New module: `src/kimi_code_plugin_cc/transcript.py`

```python
class TranscriptRecorder:
    @classmethod
    def start(cls, loop: str, meta: dict[str, object]) -> "TranscriptRecorder | None":
        """Create the run directory and write an initial run.json.

        Returns None when disabled (KIMI_TRANSCRIPTS=0) or when the base
        directory cannot be created/written. NEVER raises.
        Also performs best-effort pruning of old runs (keep newest N).
        """

    @property
    def path(self) -> str: ...            # absolute run directory

    def record_round(
        self,
        *,
        index: int,                        # 1-based loop iteration
        role: str,                         # "review" | "plan" | "primary" | "adversary"
        agent: str,                        # adapter name, or "host" for the host reviewer
        model: str | None,
        prompt: str,
        response: str,
        verdict: str | None,               # parsed verdict/status if any
        duration_s: float | None,
    ) -> None: ...                         # best-effort, never raises

    def finalize(self, *, final: dict[str, object]) -> None: ...  # best-effort
```

Internal rule: first failed write flips a private `_disabled` flag; all
subsequent calls no-op. `start()` catches everything and returns `None`.

### Storage layout

Base directory (first match wins):

1. `KIMI_TRANSCRIPT_DIR` env var, if set and usable
2. default: `Path.home() / ".kimi-code-plugin-cc" / "transcripts"`

Per run: `<base>/<run_id>/` with
`run_id = <UTC yyyymmddTHHMMSSZ>-<loop>-<6 hex from secrets.token_hex(3)>`,
e.g. `20260726T190301Z-santa-3fa9c2`. The timestamp prefix makes lexical
sort chronological (pruning relies on this).

Files per run:

- `run.json` — summary, rewritten after every round (small file; write to
  `run.json.tmp` then `os.replace`), finalized in a `finally` block:

  ```json
  {
    "schema_version": 1,
    "run_id": "20260726T190301Z-santa-3fa9c2",
    "loop": "santa",
    "agents": {"primary": "kimi", "adversary": "kimi"},
    "model": null,
    "max_iterations": 3,
    "started": "2026-07-26T19:03:01Z",
    "finished": "2026-07-26T19:14:47Z",
    "rounds": [
      {"index": 1, "role": "primary", "file": "round-01-primary.md",
       "agent": "kimi", "verdict": "request_changes", "duration_s": 41.2}
    ],
    "final": {"verdict": "red", "iterations": 3}
  }
  ```

- `round-<NN>-<role>.md` — one per recorded exchange, written immediately
  after the adapter call returns:

  ```markdown
  # round 1 - primary

  - agent: kimi
  - model: (default)
  - recorded: 2026-07-26T19:03:42Z
  - duration_s: 41.2
  - verdict: request_changes

  ## Prompt

  <verbatim prompt>

  ## Response

  <verbatim response payload>
  ```

### Configuration (document in README env table)

| Variable | Default | Meaning |
|---|---|---|
| `KIMI_TRANSCRIPTS` | `1` (on) | `0`/`false` disables recording entirely |
| `KIMI_TRANSCRIPT_DIR` | `~/.kimi-code-plugin-cc/transcripts` | base directory override |
| `KIMI_TRANSCRIPT_KEEP` | `50` | newest N run dirs kept; older pruned at start (best-effort `shutil.rmtree(..., ignore_errors=True)`); non-positive/garbage -> default |

### Wiring (exact seams, verified against `main`)

All three loops follow the same pattern; recorder creation right after
argument validation, `record_round` immediately after each `adapter.run(...)`
returns, `finalize` in a `try/finally` so crashed runs keep their rounds.

- `loops/review.py::review_loop` — the adapter call sits at the top of the
  iteration loop (`response = await adapter.run(...)`). Record with
  `role="review"`, verdict from `_build_result(...).verdict`. Wrap the
  iteration loop in `try/finally: recorder.finalize(...)`.
- `loops/santa.py::santa_loop` — two exchanges per iteration: the primary
  review and the secondary via `_secondary_review` (external adversary
  adapter OR `host_reviewer`). Record both: `role="primary"` and
  `role="adversary"`; for the host-reviewer path use `agent="host"`.
  One recorder (one run dir) covers the whole santa run. The
  `_secondary_review` helper needs the recorder (or returns enough data for
  the caller to record — implementer's choice, but recording must include
  the adversary prompt actually sent).
- `loops/planning.py::planning_loop` — `role="plan"`, `verdict=None`
  (record the status instead once known, via `finalize`).

Duration: measure with `time.monotonic()` around the adapter call in the
loop; pass the delta.

### Result models (public API change, additive only)

Add `transcript_dir: str | None = None` to `ReviewResult`, `SantaResult`,
`PlanResult`. Set it on the objects the loops return (including early
returns) when a recorder exists. The nested `primary_review` /
`secondary_review` inside `SantaResult` keep the default `None` — only the
top-level result carries the path. MCP tools already serialize via
`result.model_dump_json(indent=2)` (`mcp_server.py`), so the field reaches
the caller with **zero** changes to `mcp_server.py` success paths. Do not
change the error paths in this iteration.

## Tests (write first; extend existing patterns, fake adapters as in `tests/test_loops.py`)

New `tests/test_transcript.py`:

1. `start` creates the run dir under a `KIMI_TRANSCRIPT_DIR` override
   (tmp_path + monkeypatch) and writes an initial `run.json`.
2. `KIMI_TRANSCRIPTS=0` -> `start` returns `None`.
3. Unusable `KIMI_TRANSCRIPT_DIR` (points at an existing *file*) -> `start`
   returns `None`, no exception.
4. `record_round` writes the round file: contains prompt, response, verdict;
   content survives non-cp1252 characters (`→`) — read back with
   explicit UTF-8.
5. `run.json` after two rounds + `finalize`: `schema_version`, both round
   entries, `final` present; timestamps are ISO-8601 UTC (`Z` suffix).
6. Write failure mid-run (monkeypatch `pathlib.Path.write_text` to raise
   `OSError` after the first call): no exception escapes, subsequent calls
   no-op.
7. Pruning: create KEEP+2 fake run dirs (older timestamps), `start` removes
   the oldest two.
8. Garbage `KIMI_TRANSCRIPT_KEEP` (`"banana"`, `"-3"`) falls back to 50.
9. `run_id` is lexically sortable and matches
   `^\d{8}T\d{6}Z-[a-z]+-[0-9a-f]{6}$`.

Extend `tests/test_loops.py`:

10. `review_loop` (fake adapter, 2 iterations) -> `result.transcript_dir`
    set; exactly 2 round files + `run.json` on disk.
11. `santa_loop` -> per iteration a `round-NN-primary.md` **and**
    `round-NN-adversary.md`; host-reviewer path records `agent: host`.
12. **Crash keeps partial transcript:** fake adapter raises on iteration 2
    -> exception propagates unchanged (same type/message as today) AND
    `round-01-review.md` exists on disk.
13. **Fail-closed equivalence:** same fake-adapter scenario run twice — once
    with recording disabled, once with a recorder whose writes all fail —
    both produce identical verdict/review/iterations.
14. `planning_loop` -> plan rounds recorded, `final` carries the status.

Extend `tests/test_mcp_server.py`:

15. `run_review_loop` output JSON contains a `transcript_dir` key (string or
    null).

## Docs to update

- `README.md`: feature bullet ("every loop run leaves a transcript"), the
  three env vars in the configuration table, one sentence on where
  transcripts live and that they contain the reviewed code verbatim.
- `SECURITY.md`: note under guarantees/non-guarantees — transcripts are
  written locally, contain prompt + response verbatim, disable with
  `KIMI_TRANSCRIPTS=0`.
- `CLAUDE.md`: add `transcript.py` to the architecture list (one line).
- `CHANGELOG.md`: entry under `## [Unreleased]` (do **not** bump versions —
  release is a separate step).

## Acceptance criteria

- `uv run ruff check .` and `uv run ruff format --check .` clean.
- `uv run pytest -q` fully green; coverage stays >= 90% overall (gate: 80).
- All 15 test cases above exist and pass; test 13 (fail-closed equivalence)
  and test 12 (partial transcript on crash) are the review focus.
- No diff in `loops/prompts.py`, `extract_verdict`, `mcp_server.py`
  (success/error paths), `errors.py`.
- New files stay under 400 lines; functions under 50; no emojis; ASCII
  hyphens in code and docs.
- Work on a feature branch `feat/transcript-persistence` off `main`;
  conventional commits (`feat: ...`, `test: ...`, `docs: ...`).

## Process expectations for the implementing agent

Work test-first (write the failing tests from the list, then implement).
Follow `CONTRIBUTING.md`. If the code contradicts this plan anywhere, the
code wins — note the deviation in the commit message instead of forcing the
plan through. Do not touch `.kimi-code/`, `.work/`, CI workflows, or
version numbers. Stop and leave a note instead of guessing if an invariant
cannot be met.
