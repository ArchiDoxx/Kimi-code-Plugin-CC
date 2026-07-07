---
name: seam-design-review
description: External review of a module boundary or interface design — coupling direction, dependency rules, deep-module quality, abstraction leaks. Send the proposed design to Kimi Code and get back concrete, prioritised design findings. Single-pass, structured. For architects shaping module boundaries.
---

# seam-design-review — external module / interface design review

Use this for a **single focused review** of a proposed module boundary or
interface design (a "seam"). An external agent assesses coupling direction,
dependency rules, deep-module quality, and abstraction leaks — and returns
concrete design findings, not style nitpicks. Built for the architect shaping
module structure before implementation.

This is the design-time counterpart to `contract-audit`: where
`contract-audit` checks an existing seam against frozen wire, `seam-design-
review` checks a **proposed** boundary for soundness before you commit to it.

## When to use

- You are about to introduce a new module, layer, or public interface.
- You are refactoring a seam (moving a boundary, splitting a module).
- You want an external check on whether a module is "deep" (small interface,
  hides a real decision) or "shallow" (leaky, wide surface).
- Deciding where a new capability should live.

**Do NOT use** for an existing frozen contract (use `contract-audit`), for
concrete code bugs (use `code-review`), or for a quick A/B decision (use
`second-opinion`). For a full multi-round design plan, use `planning-loop`.

## How to use

Call the MCP tool directly (no dedicated slash command):

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<design-review brief built from the template below>"
)
```

**Read the relevant code first** (the modules the seam touches, plus any
existing layering/dependency rules) and include their **contents**. The agent
runs in an isolated worktree and cannot open host paths. Optionally pass
`model="<alias>"` (an alias from the agent CLI's own config) to route the
review to a specific provider/model; see `bridge` for details.

## The design-review brief (what gets sent)

```
You are a senior software architect reviewing a proposed module boundary /
interface design. Review for real design problems only — no style.

CONTEXT (what exists today, the modules/layering the seam touches):
---
<paste the relevant current code, module map, or dependency layout>
---

PROPOSED SEAM (the boundary or interface under review):
---
<paste the proposed interface: signatures, types, which module depends on
which, what the seam hides vs exposes, or an ASCII diagram of the layers>
---
Stated intent of the seam: <one or two sentences — what decision it should
hide, what it should let vary independently>

Review and report, grouped by issue TYPE. Each finding carries its own SEVERITY
so the seam-breaking problems surface first.

Issue types:
  - DIRECTION  — a dependency that points the wrong way (upward/across layers,
                 into internals) or creates a cycle.
  - LEAK       — an interface that exposes an implementation detail it should
                 hide (shallow module; the decision "leaks out").
  - COUPLING   — two modules joined by more than the stated seam (shared
                 mutable state, type leakage, implicit ordering).
  - MISSING    — a responsibility with no home, or a capability the seam blocks.
  - TESTABILITY— the seam as designed is hard to test in isolation.

Severity (per finding):
  - BLOCKER — the seam as designed cannot hold (wrong dependency direction,
              a cycle, or the hidden decision leaks out).
  - MAJOR   — a real coupling/leak that hurts, but the seam is salvageable.
  - MINOR   — a small leak or testability friction, low impact.

For each finding output: Type, Severity, Location (which side of the seam),
Issue (one sentence), Fix (one sentence or snippet). If the design is sound,
say so explicitly. Do NOT comment on naming or formatting; this is a design
review.
```

The agent's response is the review. Summarise the BLOCKER / MAJOR items (often
DIRECTION or LEAK) — these break the seam — and quote the concrete fixes.

## Safety defaults

- `read-only` policy. Agent cannot modify the repo.
- Isolated worktree — pass **contents**, not host paths.
- Empty / no output is surfaced as-is, never as "design is sound".

## Example

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<filled design-review brief for the new assessment/storage seam>"
)
```
