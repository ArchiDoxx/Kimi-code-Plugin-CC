# Contributing

## Setup

```bash
uv sync --extra dev
uv run kimi-code-plugin doctor   # verifies the agent CLI and configuration
```

`doctor` exits non-zero when something is actually broken, so it is safe to put
in a setup script.

## The loop

```bash
uv run ruff format .
uv run ruff check .
uv run pytest -q --cov=kimi_code_plugin_cc --cov-report=term-missing
```

CI runs exactly these on Linux and Windows against Python 3.11-3.13. Coverage is
gated at 80% (`fail_under` in `pyproject.toml`); the suite currently sits above
90%, so treat a drop as a missing test rather than a reason to lower the gate.

### Test markers

Live tests spawn the real, authenticated CLI and are excluded by default:

```bash
uv run pytest tests/test_cli_contract.py   # cheap: pins the CLI flag surface
uv run pytest -m live                      # full round-trip, needs a real CLI
```

`tests/test_cli_contract.py` skips itself when the CLI is absent, so it is safe
in CI. Run it after every agent-CLI update — it is the cheapest way to catch a
flag surface that moved.

## Principles worth knowing before you change behaviour

**Fail-closed is not negotiable.** Every path that cannot produce a confident
approval must resolve to a non-approval: unparseable output, ambiguous verdicts,
empty responses, timeouts, and crashes all end at `needs_discussion` or `red`.
If a change introduces a path where a failure could read as approval, it is a
bug regardless of how convenient it looks.

**The CLI is not pinned; the flag surface is.** The adapter depends on a small
set of flags (`-p`, `--output-format`, `-m`). Anything newer must be gated
through `agent_registry/capabilities.py` so an older CLI keeps working — never
pass a flag unconditionally because it exists on your machine.

**Verify against the tool, not memory.** Claims about the agent CLI belong in a
test or a live check, not in a comment. `capabilities.py` and
`tests/test_cli_contract.py` exist so those claims stay honest.

**Prompts are a contract.** Every loop round must restate the verdict contract.
Build prompts through `loops/prompts.py`; do not hand-roll one in a new round.

## Commits

Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.

## Releasing

The version lives in **two** files and CI fails if they disagree:

1. `pyproject.toml` → `[project].version`
2. `.claude-plugin/plugin.json` → `version`

Then:

3. Add a `CHANGELOG.md` entry describing the concrete failure each change
   prevents, not just what moved.
4. Update `README.md` if the user-facing surface changed.
5. Tag it: `git tag -a vX.Y.Z -m "..." && git push --tags`.

Step 5 is easy to forget — v1.3.1 shipped untagged.
