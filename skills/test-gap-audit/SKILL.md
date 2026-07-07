---
name: test-gap-audit
description: External audit of TESTS for a target — finds missing cases (edge cases, error paths, fail-safe paths, documented incidents) rather than reviewing code. Sends the code UNDER test plus its tests to Kimi Code and gets back concrete coverage gaps. Single-pass, structured. For devs preparing a PR against a coverage gate.
---

# test-gap-audit — external test-coverage gap audit

Use this for a **single focused audit** of the *tests* for a target. An external
agent reads the code under test together with its tests and reports the cases
that are **missing** — edge cases, error paths, fail-safe paths, and any
documented incidents the tests forget. Built for the dev preparing a PR
against a coverage gate (e.g. >= 80 %).

This is the test-focused counterpart to `code-review`: where `code-review`
hunts bugs in the code, `test-gap-audit` hunts **holes in the tests**. It
directly attacks the blind spot where the same agent writes code and its own
tests — an independent model sees the gaps the author cannot.

## When to use

- Before opening a PR — confirm the tests actually cover the behaviour you
  changed, not just the happy path.
- On safety-critical paths: verify documented incidents and a fail-safe case
  exist as named green tests.
- After a bug fix: check the regression test for the bug really exists and
  would catch it.
- Before a coverage gate — find the cheap missing cases a tool can't name.

**Do NOT use** to review code quality (use `code-review`), to review a frozen
contract (use `contract-audit`), or for an iterative multi-round pass (use
`review-loop`). For safety-critical logic where two reviewers must agree on
test sufficiency, escalate to `santa-loop`.

## How to use

Call the MCP tool directly (no dedicated slash command):

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<test-gap brief built from the template below>"
)
```

**Read both the code under test AND the test file first**, then include their
**contents**. The agent runs in an isolated worktree and cannot open host
paths. Optionally pass `model="<alias>"` (an alias from the agent CLI's own
config) to route the audit to a specific provider/model; see `bridge` for
details.

## The test-gap brief (what gets sent)

```
You are a senior test engineer auditing a test suite for GAPS — not reviewing
the code for bugs. Read the code under test, then judge whether the tests
would actually catch its failures.

CODE UNDER TEST:
---
<paste the implementation: the function/module/class under test>
---

EXISTING TESTS:
---
<paste the test file(s) for the code above>
---
[Optional] DOCUMENTED INCIDENTS / INVARIANTS that MUST have a named green test:
<paste the documented incidents and invariants, if the project documents them>

Report only MISSING coverage. For each gap output:
  - Severity: CRITICAL (a documented incident / fail-safe / security path with
               no test) / MAJOR (a real branch that can fail silently) /
               MINOR (defensive branch unlikely in practice)
  - Missing case: one sentence (what behaviour is untested)
  - Why it matters: one sentence (what breaks if this regresses)
  - Suggested test: the test name + the input that should trigger it + the
                    asserted outcome

Check specifically for: boundary/off-by-one values, null/empty/missing inputs,
error and exception paths, concurrency/ordering, stale/fault/fail-safe paths,
and any documented incident without a named test. If the tests already cover
the behaviour comprehensively, say so explicitly — do not invent gaps.
```

The agent's response is the audit. Turn each CRITICAL/MAJOR gap into a real
test (TDD-style: write the test, watch it fail or pass, keep it green).

## Safety defaults

- `read-only` policy. Agent cannot modify the repo.
- Isolated worktree — pass **contents**, not host paths.
- Empty / no output is surfaced as-is, never as "tests are sufficient".

## Example

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<filled test-gap brief for src/assessment/core.py + tests/test_assessment.py>"
)
```
