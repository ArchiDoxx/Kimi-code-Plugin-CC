# Plan: Kimi-Code-Plugin-CC v0.5

## 1. Anforderung (Restate)

Ein Claude-Code-Plugin bauen, das headless CLI-Agenten (primär Kimi Code, vorbildlich
wie das Codex-Plugin für Claude Code) in Claude Code integriert. Version 0.5 soll
alle Grundfunktionen enthalten, skalierbar für weitere Agenten/Loops sein und über
`uv` wartbar/installierbar sein. Geplante Skills wie `santa-loop` (adversariales
Dual-Review) sollen integrierbar sein.

**Scope v0.5:**
- Plugin-Manifest und -Struktur für Claude Code
- Headless Subprozess-Kommunikation zu Kimi Code CLI (stdin/stdout, JSON-Stream)
- Interne Agenten-Kommunikationslogik (Message-Passing, Session-Management)
- Erweiterbare Registry für CLI-Agenten (Kimi, Codex, Gemini, …)
- Skills für Planungs-, Review- und Kommunikations-Loops
- Python-Backend mit `uv` (Dependencies, Scripts, Publishing)
- Tests mit pytest, Qualitäts-Gate mit ruff

## 2. Empfohlener Ansatz

**Generische CLI-Agent-Bridge** — nicht nur Kimi Code, sondern ein Plugin-Framework,
das beliebige headless CLI-Agenten als Subagenten in Claude Code einbindet. Kimi Code
ist das erste implementierte Backend.

**Warum dieser Ansatz?**
- Erfüllt die Skalierbarkeitsanforderung aus der Aufgabe.
- Spiegelt das Codex-Plugin-Vorbild wider (ein externes CLI in Claude Code nutzen).
- Ermöglicht später einfaches Hinzufügen von Review-/Planungs-Loops durch neue Skills
  und Agenten-Adapter.
- `uv` eignet sich hervorragend für Python-Package-Management und -Distribution.

## 3. Architektur

```
kimi-code-plugin-cc/
├── .claude-plugin/
│   ├── plugin.json              # Manifest
│   └── marketplace.json         # Lokaler Marketplace-Eintrag
├── .mcp.json                    # MCP-Server-Konfiguration (uv run ...)
├── agents/                      # Claude-Code-Agent-Definitionen
│   ├── bridge-orchestrator.md
│   └── review-adversary.md
├── commands/                    # Slash-Commands
│   ├── kimi-run.md
│   └── kimi-review.md
├── skills/                      # Claude-Code-Skills
│   ├── bridge/
│   │   └── SKILL.md
│   ├── planning-loop/
│   │   └── SKILL.md
│   ├── review-loop/
│   │   └── SKILL.md
│   └── santa-loop/
│       └── SKILL.md
├── src/
│   └── kimi_code_plugin_cc/
│       ├── __init__.py
│       ├── cli.py               # Haupt-CLI / Server-Start
│       ├── bridge/
│       │   ├── __init__.py
│       │   ├── runner.py        # Subprozess-Management (Windows-aware)
│       │   ├── parser.py        # JSON-Stream / NDJSON Parsing
│       │   ├── session.py       # Session-State + Depth-Guard
│       │   └── acp_adapter.py   # ACP-Adapter-Skelett für v0.6
│       ├── agent_registry/
│       │   ├── __init__.py
│       │   ├── base.py          # Abstract Agent Adapter (mit Approval-Policy)
│       │   ├── kimi.py          # Kimi Code Adapter (Kommando gepinnt)
│       │   └── codex.py         # Codex Adapter (Skelett als Design-Probe)
│       ├── protocol/
│       │   ├── __init__.py
│       │   └── messages.py      # Nachrichten-Schema: depth, bridge_id, approval_policy
│       ├── loops/
│       │   ├── __init__.py
│       │   ├── planning.py
│       │   ├── review.py
│       │   └── santa.py         # Heterogene Reviewer (z. B. Kimi + Codex)
│       ├── security/
│       │   ├── __init__.py
│       │   └── policy.py        # Approval-Policy, Allowlist, Worktree-Isolation
│       └── mcp_server.py        # MCP-Server
├── tests/
│   ├── test_bridge.py
│   ├── test_protocol.py
│   ├── test_agent_registry.py
│   ├── test_loops.py
│   ├── test_security.py
│   └── test_windows_runner.py
├── pyproject.toml               # uv-Projekt-Konfiguration
├── README.md
├── CLAUDE.md
├── LICENSE
└── docs/
    └── decisions/               # ADRs / Entscheidungslog
        ├── 001-session-model.md
        ├── 002-approval-policy.md
        └── 003-recursion-guard.md
```

## 4. Schritte

1. **Projekt-Setup mit uv**
   - `pyproject.toml` mit uv-Metadata, Dependencies, Scripts
   - Initiales Package unter `src/kimi_code_plugin_cc/`
   - ruff + pytest konfigurieren
   - Ergebnis: `uv run pytest` läuft (noch leer).

2. **Plugin-Manifest und Claude-Code-Struktur**
   - `.claude-plugin/plugin.json` mit `skills`, `agents`, `commands`
   - `.mcp.json` mit `uv run kimi-code-plugin-mcp`
   - `CLAUDE.md` mit Projektkontext
   - Erste Skill-/Agent-/Command-Skelette
   - Ergebnis: Plugin ist in Claude Code ladbar.

3. **Protokoll- und Session-Schicht**
   - Pydantic-Modelle für Agent-Nachrichten mit Feldern:
     - `bridge_id`, `depth` (Rekursionstiefe)
     - `approval_policy` (read-only, accept-edits, explicit)
   - Session-IDs, Turn-Counter, State
   - **Depth-Guard**: `KIMI_BRIDGE_DEPTH` (Default 2). `runner.py` injiziert
     `KIMI_BRIDGE_DEPTH=depth+1` in die Child-Env; der Adapter bricht ab, wenn
     das Limit überschritten ist.
   - **Re-Entry-Test**: ein gespawnter Agent erbt `depth+1` und blockiert beim
     Selbst-Aufruf.
   - Ergebnis: Unit-Tests für Nachrichten/Session/Depth-Guard/Re-Entry grün.

4. **Subprozess-Bridge für Kimi Code**
   - Adapter-Klasse für Kimi Code CLI
   - Gepinntes Kommando (verifiziert mit kimi 0.17.1):
     `kimi -p "<prompt>" --output-format stream-json`
     (`--print`, `--final-message-only`, `--afk` existieren nicht.)
   - Per-Turn-Spawn (v0.5): jede Anfrage startet einen neuen Prozess.
   - Windows-aware: `asyncio.create_subprocess_exec` mit argv-Liste +
     `shell=False` (kein `shlex`, kein Shell-String); ProactorEventLoop auf
     Windows.
   - NDJSON/Stream-Parser mit Zeilen-Buffering über Chunk-Grenzen, Timeout und
     klarer Fehlerbehandlung
   - **Auth-Fast-Fail**: Per-Turn-`kimi -p` ohne Auth kann hängen →
     Auth-Precheck + Timeout, damit der Swarm nicht blockierte Prozesse spawnt
   - Ergebnis: `kimi-run` Skill kann eine einfache Frage an Kimi Code senden
     und die Antwort zurückliefern.

5. **Sicherheit: Capability-Restriction + Policy-Obergrenze**
   - Reale Kimi-Flags (0.17.1): `-y/--yolo` und `--auto` sind getrennte
     Opt-in-Flags und per Default `false`. `kimi -p` ohne diese Flags führt
     Tool-Calls nicht auto-approved aus.
   - **Hebel für v0.5**:
     - Kein `--yolo`/`--auto` als Default verwenden.
     - **Verbindliche Worktree-Isolation**: jeder Agent läuft in einem
       temporären, vom Host-Repo getrennten Verzeichnis; Schreibzugriff auf
       das Host-Repo nur über explizite, vom User freigegebene Mounts.
   - **ACP als v0.6-Pfad**: `kimi acp` mediiert Tool-Call-Permissions über die
     Bridge; ADR-001 hält die Abwägung Spawn vs. ACP vs. `kimi server` fest.
   - **Policy-Obergrenze**: `KIMI_MAX_POLICY` aus Env/Config; `run_agent`
     erzwingt sie unabhängig vom angefragten Wert. Eskalation darüber =
     Human-in-the-Loop.
   - Default-Policy: **read-only**; `accept-edits` nur bis zur Obergrenze und
     niemals mit `--yolo`/`--auto`.
   - Ergebnis: Security-Tests für Capability-Beschränkung und Policy-Cap grün.

6. **Agent-Registry**
   - Abstrakte Basis + Registrierung
   - Kimi-Adapter konkret mit gepinntem Kommando
   - Codex-Adapter als Skelett, um die Abstraktion gegen das echte
     `codex exec`-Interface zu validieren
   - Ergebnis: Neue Agenten lassen sich mit einer Klasse hinzufügen.

7. **Loop-Skills (Planung / Review / Santa)**
   - `planning-loop`: strukturierte Planungsaufforderung mit `max_iterations`
   - `review-loop`: Code-Review durch externen Agenten mit `max_iterations`
   - `santa-loop`: adversariales Dual-Review mit **heterogenen** Reviewern.
     In v0.5 ist der zweite Reviewer **Claude selbst** (immer verfügbar, echt
     heterogen); Codex bleibt Adapter-Skelett. Bei Nicht-Konvergenz innerhalb
     `max_iterations` fail-closed blockieren (Safety-Bias: nie „grün durchwinken").
   - Ergebnis: Skills sind aufrufbar und durch Tests abgedeckt.

8. **MCP-Server**
   - Tool `run_agent(agent_name, prompt, approval_policy)` — `approval_policy`
     wird gegen `KIMI_MAX_POLICY` gecappt.
   - Start via `uv run --project ${CLAUDE_PLUGIN_ROOT} kimi-code-plugin-mcp`,
     damit das Package unabhängig vom cwd des Nutzer-Repos gefunden wird.
   - `.mcp.json` verweist auf diesen Command unter Nutzung von
     `${CLAUDE_PLUGIN_ROOT}`.
   - Ergebnis: Claude Code kann das Plugin auch über MCP ansprechen.

9. **Dokumentation, Tests, Quality-Gate, Live-Smoke**
   - README mit Installations- und Nutzungsanleitung
   - **ADR-001**: Session-Modell — explizite Abwägung Spawn vs. ACP vs.
     `kimi server` mit echten Kimi-Flags; Begründung für Per-Turn-Spawn in v0.5
     und ACP als v0.6-Pfad.
   - ADRs: Approval-Policy/Capability-Restriction, Rekursionsschutz
   - Test-Coverage ≥ 80 % für Kernlogik
   - `ruff check` und `ruff format` sauber
   - **Live-Smoke-Test**: dokumentierter manueller Test, bei dem ein echter
     Prompt über `/kimi-run` round-tripped wird
   - Ergebnis: Plugin ist installierbar, getestet, dokumentiert und rauchgetestet.

## 5. Risiken & Edge Cases

- **Authentifizierung**: Headless Kimi Code / Claude Code erfordern ggf. Token.
  → Erste Version setzt voraus, dass die CLI bereits authentifiziert ist.
- **Stream-Parsing**: Unvollständige JSON-Lines, Timeouts.
  → Robuster Parser mit Retry und klarer Fehlermeldung.
- **Sicherheit**: Subprozess-Aufrufe müssen parametrisiert sein.
  → Keine String-Konkatenation für CLI-Argumente.
- **Rekursion**: Agent ruft Agent ruft Agent → Depth-Guard in Protokoll und Runner.
- **Auto-Approve**: Kimi `-y/--yolo` und `--auto` sind Opt-in (Default false);
  Default-Spawn verwendet sie nicht. Trotzdem verbindliche Worktree-Isolation.
- **Permission-Mediations-Lücke**: Per-Turn-Spawn kann laufende Tool-Calls nicht
  interaktiv bremsen. → ACP als v0.6-Pfad; v0.5 durch read-only Default + Isolation.
- **Policy-Eskalation**: Aufrufer könnte `accept-edits` erzwingen wollen.
  → `KIMI_MAX_POLICY` als nicht vom Modell anhebbare Obergrenze.
- **Nicht-Konvergenz**: Loops könnten endlos iterieren.
  → `max_iterations` pro Loop + fail-closed (blockieren, nie grün).
- **Skalierbarkeit**: Zu viele parallele Subagenten.
  → Semaphore / max-concurrency in der Bridge (ergänzend zum Depth-Guard).

## 6. Teststrategie

- **TDD** für `protocol/`, `bridge/`, `agent_registry/`, `loops/`, `security/`
- Mocks für CLI-Subprozesse
- Windows-Subprozess-Test
- Fail-Safe-Tests: Agent nicht verfügbar → sauberer Fehler, kein Crash
- Depth-Guard-Test + **Re-Entry-Test**: env-propagierte `depth+1` blockiert
  Selbst-Aufruf
- Policy-Test: Default read-only, `accept-edits` wird gegen `KIMI_MAX_POLICY`
  gecappt
- Capability-Restriction-Test: Worktree-Isolation, Tool-Set-Beschränkung
- Auth-Fast-Fail-Test: unauthentifizierter Agent failt schnell mit Timeout
- Integrationstest: Lokaler "Dummy-Agent" via Echo-Skript
- **Live-Smoke-Test**: Echter Kimi-CLI-Prompt round-trip (manuell, dokumentiert)

## 7. Definition-of-Done für v0.5

v0.5 gilt als fertig, wenn alle folgenden Kriterien erfüllt sind:

1. Echter Prompt round-trips via `/kimi-run` (Live-Smoke).
2. `/kimi-review` im `santa-loop` konvergiert und blockiert bei Dissens korrekt.
3. Depth-Guard + Re-Entry-Test greifen (env-propagierte Tiefe > Limit wird
   abgelehnt).
4. Coverage ≥ 80 % für Kernlogik (`protocol`, `bridge`, `security`, `loops`).
5. `ruff check` und `ruff format` sind sauber.
6. Plugin lädt erfolgreich in Claude Code (`/plugin list`).
7. MCP-Server startet via
   `uv run --project ${CLAUDE_PLUGIN_ROOT} kimi-code-plugin-mcp` und exponiert
   `run_agent`.
8. ADRs für Session-Modell, Approval-Policy/Capability-Restriction und
   Rekursionsschutz liegen vor.

## 8. Nächster Schritt

Nach Freigabe des Plans:
1. Feature-Branch anlegen (`git checkout -b feat/v0.5-core`).
2. Step 1 (uv-Setup) umsetzen.
3. Remote konfigurieren; Push/PR nur nach Freigabe durch Lucas.
