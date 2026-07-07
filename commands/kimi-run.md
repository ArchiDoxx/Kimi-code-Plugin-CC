---
description: Run a single prompt through a registered headless CLI agent (Kimi by default) via the bridge MCP server. Lowest-overhead one-shot call.
argument-hint: [agent-name] "<prompt>" [--model <alias> | [<model-alias>]]
allowed-tools: mcp__kimi-code-plugin-cc__run_agent, Read
---

Run one prompt through a headless CLI agent using the bridge.

Arguments (raw): `$ARGUMENTS`

Do the following:

1. Parse the arguments:
   - If the first whitespace-delimited token is a bare word with no spaces and
     is followed by a quoted string, treat it as the agent name and the rest as
     the prompt. Otherwise the agent name is `kimi` and the whole of
     `$ARGUMENTS` is the prompt.
   - An optional `--model <alias>` anywhere in the arguments selects a model
     alias from the agent CLI's own config (multi-provider setups, e.g. a GLM
     alias like `zai-coding-plan/glm-5.2`). Remove it from the prompt text.
   - Equivalent shorthand: a trailing standalone bracketed token like
     `[glm-4.6]` after the prompt selects the same way. Strip it from the
     prompt. Aliases must match `[A-Za-z0-9][A-Za-z0-9._:/-]*` (no
     whitespace); normalize loose names like `[GLM 4.6]` → `glm-4.6` and
     state which alias you passed. Do NOT treat brackets inside the quoted
     prompt text as a model selector.
2. If the prompt references a file, **Read the file** and include its contents
   in the prompt. The agent runs in an isolated worktree and cannot open host
   paths.
3. Call `mcp__kimi-code-plugin-cc__run_agent` with:
   - `agent_name`: the resolved agent (default `kimi`)
   - `prompt`: the resolved prompt
   - `approval_policy`: `read-only` (do NOT raise this unless the user
     explicitly asked and is authorized; the server caps it at `KIMI_MAX_POLICY`)
   - `model`: only when `--model` or a bracketed `[<alias>]` was given
     (omitted = the CLI's default model)
4. Return the agent's payload verbatim, then add a one-line note of which agent
   and policy were used.

Safety:
- Never pass `--yolo`, `--auto`, or any auto-approve flag; the adapter forbids
  them structurally.
- The agent runs in an isolated worktree; it cannot implicitly write the repo.
- If the tool errors (unknown agent, auth hang/timeout), report the error
  plainly. Do not retry with a higher policy.
- For a focused code review use `/kimi-code-review`; for an iterative review
  loop use `/kimi-review <target> --loop review`.
