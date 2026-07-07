---
name: fail-safe-audit
description: External audit of every failure / stale / fault path for safe-state behavior (never the safe / "all-clear" state on failure). Sends the code to Kimi Code and gets back each path that can reach an unsafe state. Single-pass, structured, empty-output-safe (never reports OK on failure). For devs and architects working on safety-relevant logic.
---

# fail-safe-audit — external failure-path safe-state audit

Use this for a **single focused audit** of the failure behaviour of a target.
An external agent walks every failure / stale / fault / timeout / empty-input
path and reports each one that can reach an **unsafe** state (e.g. any path
that can emit `green` / the "all-clear" on stale, fault, or missing data).
Built for devs and architects working on safety-relevant logic.

This is the invariant-focused counterpart to `code-review`: where `code-review`
looks broadly, `fail-safe-audit` looks at **one thing** — does every failure
end in a safe state? It is deliberately narrow so a focused lens catches what a
general review glazes over.

## When to use

- The target is on the critical path (assessment / alarm / ingestion logic).
- After touching any error, timeout, stale-detection, or fault-handling branch.
- Before declaring a module fail-safe for a milestone / release.
- Whenever you changed the "unknown / degraded" behaviour of the system.

**Do NOT use** for general code quality (use `code-review`), for missing tests
(use `test-gap-audit`), or for a contract-vs-code check (use
`contract-audit`). Because the target is safety-relevant, prefer
`santa-loop` (dual-review, fail-closed) for the final sign-off — this skill is
the focused single-pass audit that feeds it.

## How to use

Call the MCP tool directly (no dedicated slash command):

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<fail-safe brief built from the template below>"
)
```

**Read the target code first** and include its **contents**. The agent runs in
an isolated worktree and cannot open host paths. Optionally pass
`model="<alias>"` (an alias from the agent CLI's own config) to route the
audit to a specific provider/model; see `bridge` for details.

## The fail-safe brief (what gets sent)

```
You are a senior safety/reliability engineer auditing code for fail-safe
behaviour. The system's fail-safe rule: on ANY failure, stale data, sensor
fault, timeout, or missing input, the system must NEVER emit the safe /
"all-clear" state. It must fall back to a safe/degraded state (e.g. "unknown"
or a warning) — never the all-clear, and never fail silently.

CODE UNDER AUDIT:
---
<paste the code that decides the risk level / state, including every try/except,
stale check, timeout, default, and fallback>
---

SAFE STATE (what must NEVER appear on failure): <e.g. risk_level == "green">
SAFE FALLBACK (what SHOULD appear on failure):  <e.g. risk_level == "unknown">

Walk EVERY failure / degraded path and report only paths that can reach the
unsafe state. For each finding output:
  - Severity: BLOCKER (reaches the all-clear on failure) / MAJOR (fails
              silently — no state, swallowed error) / MINOR (degraded but not
              worst-case-safe)
  - Path: the exact sequence (input/condition -> branch -> emitted state)
  - Why it is unsafe: one sentence
  - Fix: one sentence or snippet (what the safe default must be)

Check specifically for: except blocks that continue, defaults that are the
safe state, stale/timeout branches that fall through, None/empty that map to
green, sensor_status=fault that is ignored, and any silent swallow of errors.
If every failure path ends in a safe state, say so explicitly — do not invent
issues. A missing failure path you cannot rule out is itself a finding.
```

The agent's response is the audit. Every BLOCKER is a must-fix before merge.
For the final sign-off on safety-critical code, run the same target through
`santa-loop` so two independent reviewers must agree.

## Safety defaults

- `read-only` policy. Agent cannot modify the repo.
- Isolated worktree — pass **contents**, not host paths.
- Empty / no output is surfaced as-is — it is **never** read as "fail-safe OK".

## Example

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<filled fail-safe brief for src/assessment/core.py>"
)
```
