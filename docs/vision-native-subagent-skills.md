# Vision: Native Subagent-Skills (Plugin-Evolution)

> **Status:** Vorhaben gepinnt — noch keine Planung, kein Design, kein Termin.
> Hier festgehalten, damit die Idee nicht verloren geht und später iterativ
> ausgearbeitet werden kann.

## Motivation

Das Plugin brückt aktuell externe Headless-CLI-Agenten (Kimi Code, Codex-Skeleton)
über einen MCP-Server in Claude Code hinein. Das bedeutet: **jeder Nutzer braucht
die Kimi Code CLI** (kommerziell, lizenziert), um die Skills und Commands des
Plugins nutzen zu können.

Das schließt eine große Nutzergruppe aus: Claude-Code-User, die **keine eigene
Kimi-CLI-Lizenz** haben, aber dennoch von den Review-, Planning- und
Santa-Loop-Fähigkeiten des Plugins profitieren möchten.

## Vorhaben

Einen **zweiten, parallelen Ausführungspfad** schaffen, der Claude Codes
**nativen Subagenten-Dispatch** (`Agent`-Tool) verwendet — ohne externe CLI,
ohne API-Key, ohne zusätzliche Laufzeitabhängigkeit.

Die bestehenden MCP-basierten Skills bleiben unangetastet. Es entsteht eine
**parallele Skill-Familie** für den nativen Pfad.

## Machbarkeit

**Ja, es ist möglich — und die Architektur ist dafür bereit.**

Die aktuelle Plugin-Architektur hat zwei saubere Ebenen:

1. **MCP-Server-Ebene** (`AgentAdapter`-Registry in `src/kimi_code_plugin_cc/agent_registry/`):
   Hier ist ein weiterer Adapter theoretisch denkbar, aber der MCP-Server kann
   den Host-LLM nicht direkt aufrufen (MCP ist serverseitig). Das würde direkte
   Anthropic-API-Aufrufe brauchen (API-Key, Kosten) — **nicht der gewählte Pfad**.

2. **Skills/Commands-Ebene** (`skills/`, `commands/`): Reine Markdown-Anleitungen.
   Diese können so erweitert werden, dass sie den nativen `Agent`-Tool-Dispatch
   verwenden statt des MCP-Tools `run_agent`. **Das ist der gewählte Pfad.**

Die existierenden `agents/*.md`-Dateien (`bridge-orchestrator.md`,
`review-adversary.md`) sind bereits Claude-Code-Subagenten-Definitionen und
zeigen, dass das Konzept im Plugin bereits angelegt ist.

## Gewählte Richtung: Native Subagent-Skills

| Aspekt | MCP-Pfad (bestehend) | Nativer Pfad (neu) |
|---|---|---|
| Laufzeit | Kimi Code CLI (extern) | Claude Code Host (intern) |
| Abhängigkeit | Kimi-CLI-Lizenz nötig | Keine zusätzliche |
| Loop-Orchestrierung | Python (`loops/*.py`, deterministisch) | Host-LLM (prompt-basiert) |
| Modell-Auswahl | `config.toml`-Alias (`-m glm-4.6`) | Native Claude-Code-Modell-Auswahl |
| Isolation | Worktree, Env-Allowlist, Depth-Guard | Host-Workspace (geteilt) |
| Zielgruppe | Kimi-CLI-Nutzer | Alle Claude-Code-User |

## Was später ausgearbeitet werden muss

- **Neue Subagenten-Definitionen** (`agents/native-reviewer.md`, `agents/native-planner.md`, …)
- **Neue Skill-Varianten** oder Skill-Sektionen, die den nativen Dispatch erklären
- **Neue Commands** (`/native-run`, `/native-review`, …)
- **Modell-Auswahl-Mechanik** für den nativen Pfad (z.B. Opus für Review,
  Sonnet/Fable für Code gezielt ansprechen)
- **Loop-Approximationen** (Review-Loop, Santa-Loop, Planning-Loop) als
  prompt-basierte Orchestrierung durch den Host
- **Dokumentation** für Nutzer: wann welchen Pfad nutzen

## Begrenzungen (bewusst akzeptiert)

- Die strukturellen Garantien der Python-Loops (fail-closed, depth-guard,
  env-allowlist) können im nativen Pfad nur prompt-basiert approximiert werden.
- Die Worktree-Isolation entfällt; Subagenten teilen den Host-Workspace.
- Das sind bewusste Trade-offs für den Gewinn der Unabhängigkeit von der CLI.

## Nächste Schritte (wenn dieser Punkt prioritisiert wird)

1. Brainstorming/Design-Session für die native Skill-Familie
2. Implementierungs-Plan (`docs/plan-native-subagent-skills.md`)
3. Prototyp: ein nativer Skill + ein nativer Subagent als Proof-of-Concept
4. Ausrollen auf die übrigen Skills

---

*Gepinnt am 2026-07-27. Keine Commit-Zusage, kein Termin — nur Festhaltung
der Evolutionsrichtung.*
