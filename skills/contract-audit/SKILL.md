---
name: contract-audit
description: External audit of a frozen API / data-model contract against its implementation. The architect owns the team — this skill sends the frozen contract AND the implementing code to Kimi Code and gets back field-by-field drift, missing pieces, and fail-safe coverage gaps. Single-pass, structured. For architects who own a team.
---

# contract-audit — external contract drift & consistency audit

Use this for a **single focused audit** of a frozen API or data-model contract
(the "team") against the code that implements it. An external agent compares
contract and implementation field-by-field / endpoint-by-endpoint and reports
drift, missing pieces, and fail-safe gaps. Built for the architect who owns
the team.

This is the architect's counterpart to `code-review`: where `code-review` looks
at code quality, `contract-audit` looks at **contract fidelity** — does the
wire reality match the frozen spec, and does every contract guarantee hold?

## When to use

- After freezing or changing an API / data-model contract (before it ships to
  other teams).
- Before a milestone gate where the team must be stable.
- When you suspect the implementation has drifted from the frozen spec.
- Reviewing a contract for internal consistency, completeness, and fail-safe
  coverage before signing it off.

**Do NOT use** for general code quality — use `code-review`. For a single
decision trade-off, use `second-opinion`. If two independent reviewers must
agree on a contract change (safety-critical team), escalate to `santa-loop`.

## How to use

There is no dedicated slash command — call the MCP tool directly:

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<audit brief built from the template below>"
)
```

**Read both the contract and the implementation first**, then include their
**contents** in the prompt. The agent runs in an isolated worktree and cannot
open arbitrary host paths.

Optionally route to a different model — e.g. a stronger one for a final-team
sign-off. The alias must exist in the agent CLI's own config
(`~/.kimi-code/config.toml`); see `bridge` for details:

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<audit brief>",
  model="moonshot-ai/kimi-k2.6"
)
```

## The audit brief (what gets sent)

Wrap the contract + implementation in this brief so the external agent returns
structured, drift-focused findings instead of generic prose:

```
You are a senior software architect auditing a frozen API/data-model contract
against its implementation. Report REAL contract issues only.

CONTRACT (frozen, source of truth):
---
<paste the contract: OpenAPI paths/components, schema.sql, or Pydantic models
that define the wire format — the fields, types, enums, status codes, error
shape, versioning rule>
---

IMPLEMENTATION (the code that should fulfil the contract):
---
<paste the implementing code: routes/handlers, repository layer, enums,
serializers — whatever produces the wire output>
---

Audit and report, grouped by issue TYPE. Each finding carries its own SEVERITY
so the blocker-class drift surfaces first.

Issue types:
  - DRIFT     — implementation contradicts a frozen field/type/enum/status
                code/error shape. State the contract clause and the code line.
  - MISSING   — a contract field/endpoint/guarantee with no implementation, or
                an implementation with no contract backing.
  - FAIL-SAFE — a contract invariant (e.g. "stale or fault -> never the safe
                state") that the code can violate. Show the path.
  - AMBIGUITY — contract wording the implementation could reasonably read two
                ways.

Severity (per finding):
  - BLOCKER — a frozen contract guarantee is violated (wire would not match
              the spec), or a fail-safe invariant can break.
  - MAJOR   — real drift that another team/consumer would hit, but no
              guarantee broken.
  - MINOR   — cosmetic mismatch (doc/comment vs field), no wire impact.

For each finding output: Type, Severity, Contract clause, Code location,
Issue (one sentence), Fix (one sentence or snippet). If the contract and
implementation agree, say so explicitly — do not invent issues. Do NOT comment
on style; this is a contract audit.
```

The agent's raw response is the audit. Summarise the BLOCKER / MAJOR items
(often DRIFT or FAIL-SAFE) for the user — those are the blockers — and quote
the concrete fixes.

## Safety defaults

- `read-only` policy (agent cannot modify the repo).
- Agent runs in an isolated worktree — that is why you must pass **contents**,
  not host paths.
- Empty / no output is surfaced as-is, never as "contract OK".

## Example

```
# (read both files first, then paste contents into the brief)
contract:   docs/api/v1/openapi.yaml   + src/model/enums.py
impl:       src/api/routes.py          + src/api/sse.py

mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="<filled audit brief>"
)
```
