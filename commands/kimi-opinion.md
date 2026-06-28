---
description: Quick external second opinion from Kimi Code on a design decision, approach, or trade-off. Decisive, trade-off-aware answer in one shot.
argument-hint: "<question>" [--file <path>]
allowed-tools: mcp__kimi-code-plugin-cc__run_agent, Read
---

Get a quick external second opinion on a design decision.

Arguments (raw): `$ARGUMENTS`

Do the following:

1. Parse `$ARGUMENTS`:
   - `--file <path>`: if present, **Read that file** and include its contents as
     context. Remove the flag and the path from the question.
   - The rest is the user's question.
2. Build the opinion brief:

   ```
   You are a senior engineer giving a quick second opinion. Be decisive.

   Question:
   <the user's question>

   [If a file was provided:]
   Context code:
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

3. Call `mcp__kimi-code-plugin-cc__run_agent` with:
   - `agent_name`: `kimi`
   - `prompt`: the opinion brief
   - `approval_policy`: `read-only`
4. Return the agent's answer. If it hedged or gave no clear recommendation, say
   so plainly rather than fabricating a decision.

Safety:
- `read-only` policy. Never pass `--yolo` / `--auto`.
- For a full code review use `/kimi-code-review`; for multi-round planning use
  the `planning-loop` skill.
