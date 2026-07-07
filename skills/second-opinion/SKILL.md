---
name: second-opinion
description: Quick external second opinion on a design decision, approach, or trade-off. One-shot call to Kimi Code with a decision-focused prompt. Use /kimi-opinion "<question>" for fast design/architecture feedback before you commit to an approach.
---

# second-opinion — fast design / decision check

Use this when you are about to commit to a design decision and want a quick
**external sanity check** before you do. One shot, no loop. It is the skill
behind `/kimi-opinion`.

## When to use

- "Should I use approach A or B here?"
- "Is this the right abstraction, or am I over-engineering?"
- "What's the trade-off between X and Y for this module?"
- "Does this design smell right to you?"

**Do NOT use** for reviewing concrete code (use `code-review`) or for deep
iterative planning (use `planning-loop`).

## How to use

**Slash command (preferred):**

```
/kimi-opinion "<question>"
```

Optionally include context: `/kimi-opinion "<question>" --file <path>` — the
command reads the file and includes its contents so the agent has the real
code, not just a description.

**MCP tool directly:** call `mcp__kimi-code-plugin-cc__run_agent` with
`agent_name="kimi"` and a prompt built from the template below.

## The opinion brief (what gets sent)

The skill/command wraps the question so the external agent gives a decisive,
trade-off-aware answer instead of hedging:

```
You are a senior engineer giving a quick second opinion. Be decisive.

Question:
<the user's question>

[Optional context code:]
---
<file contents>
---

Answer with:
  1. Recommendation: one clear pick (A or B, or a specific approach). No "it depends".
  2. Why: 2-3 sentences on the deciding factor.
  3. The main risk of your recommendation.
  4. When you would flip to the alternative (the trigger condition).

Do not hedge. If the question is genuinely under-specified, say exactly what
one fact would change your answer.
```

Return the agent's answer. If it hedged or gave no clear recommendation, tell
the user plainly — do not dress it up as a decision.

## Safety defaults

- `read-only` policy. Agent cannot modify the repo.
- Isolated worktree — pass file **contents**, not host paths.

## Example

```
/kimi-opinion "Should this bridge use asyncio.create_subprocess_exec or subprocess.run?"
/kimi-opinion "Is the depth-guard sufficient or should I add a semaphore too?" --file src/kimi_code_plugin_cc/bridge/runner.py
```
