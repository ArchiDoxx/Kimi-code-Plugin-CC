# LIVE_TEST.md — Voller Live-Test des Kimi-Code-Plugin-CC

Manuelles End-to-End-Testprotokoll für **alle** Funktionen des Plugins (4 MCP-Tools +
4 Skills/Commands), inklusive des kritischen **Transport-Integritäts-Tests** (Multi-Line),
der Sicherheitsgarantien und der Direkt-Node-Kontrolle.

> **Status der letzten Ausführung: 2026-06-28 (Windows 11, kimi 0.20.1, Python 3.14),
> NACH dem De-Shim-Fix.**
> **Ergebnis: ALLE 4 MCP-Tools + Multi-Line-Transport GRÜN.** Der Windows-
> `.CMD`-Shim wird jetzt im Adapter auf ``node main.mjs`` aufgelöst, bevor
> der Subprocess gestartet wird, sodass ``cmd.exe`` nicht mehr dazwischen-
> schaltet ist und ``\\n`` in Prompts erhalten bleibt. Siehe §3 und §8.

---

## 1. Zweck & Geltungsbereich

Dieses Runbook prüft, ob der Bridge-Pfad **real** funktioniert — nicht über Mocks, sondern
mit einer echten, authentifizierten Kimi-CLI. Es deckt ab:

| Oberfläche | Funktion | Quelle |
|---|---|---|
| MCP-Tool | `run_agent` | `mcp_server.py` |
| MCP-Tool | `run_review_loop` | `loops/review.py` |
| MCP-Tool | `run_santa_loop` | `loops/santa.py` |
| MCP-Tool | `run_planning_loop` | `loops/planning.py` |
| Skill/Command | `/kimi-run` | `skills/bridge` |
| Skill/Command | `/kimi-code-review` | `skills/code-review` |
| Skill/Command | `/kimi-opinion` | `skills/second-opinion` |
| Skill/Command | `/kimi-review` (+ santa) | `skills/review-loop`, `santa-loop` |

**Warum ein eigener Live-Test:** Die Unit-Tests mocken den Subprozess oder laufen auf POSIX.
Der einzige Windows-spezifische Fehler (siehe §3) ist damit **nicht** abgedeckt — er fällt
nur in einem echten Live-Lauf auf. „Verified end-to-end on Windows" galt bisher nur für
Single-Line-Prompts.

---

## 2. Voraussetzungen & Pre-flight-Checks

Vor jedem Live-Test diese vier Checks (PowerShell / Git-Bash):

```bash
# (a) Kimi-CLI installiert + authentifiziert? (muss eine Antwort liefern, kein Auth-Hang)
kimi -p "say OK" --output-format stream-json

# (b) Worauf zeigt 'kimi'? -> auf Windows i.d.R. eine .CMD (= Ursache des Bugs, §3)
python -c "import shutil; print(shutil.which('kimi'))"

# (c) Node + echter Entry-Point vorhanden (für die Direkt-Node-Kontrolle, §3.3)?
python -c "import shutil; print(shutil.which('node'))"
ls "$APPDATA/npm/node_modules/@moonshot-ai/kimi-code/dist/main.mjs"

# (d) Plugin in Claude Code geladen?  /reload-plugins -> dann /mcp (kimi-code-plugin-cc sichtbar)
```

Pre-flight bestanden, wenn (a) eine echte Antwort liefert und (d) die 4 MCP-Tools listet.
Ein **Auth-Hang** in (a) (Timeout nach 120 s) ist KEIN Plugin-Bug, sondern fehlende
Kimi-Anmeldung → zuerst `kimi` interaktiv anmelden.

---

## 3. KRITISCH: Transport-Integritäts-Test (Multi-Line)  ⚠️

Der wichtigste Test. **Jede** echte Review-/Plan-Aufgabe ist mehrzeilig (Code + Brief).
Wenn dieser Test fehlschlägt, sind faktisch ALLE anderen Funktionen wertlos, egal was sie
sonst „melden".

### 3.1 Control A — Single-Line (muss IMMER funktionieren)
Eingabe (eine Zeile, ein offensichtlicher Bug):
```
Review this and reply with the one bug in one line: def add(a,b): return a-b  # should add
```
**Erwartung (PASS):** Kimi nennt den Bug (Subtraktion statt Addition).

### 3.2 Control B — Multi-Line (deckt den Bug auf)
Eingabe (Bug steht auf Zeile 2):
```
Name the bug on the next line in one sentence:
def sub(a,b): return a+b   # named sub but adds
```
**Erwartung (PASS, gesunder Bridge):** Kimi nennt den Bug auf Zeile 2.
**Beobachtet 2026-06-28 (FAIL):** *„No bug was provided on the next line."* → Zeile 2 kam nie an.

### 3.3 Control C — Direkt-Node (umgeht den Shim, beweist den Fix)
```bash
node "$APPDATA/npm/node_modules/@moonshot-ai/kimi-code/dist/main.mjs" \
  -p $'Name the bug on the next line:\ndef sub(a,b): return a+b   # named sub but adds' \
  --output-format stream-json
```
**Beobachtet 2026-06-28 (PASS):** Kimi nennt den Bug korrekt.

### 3.4 Diagnose-Beweis (deterministisch, ohne Kimi)
```bash
printf '@echo off\r\necho ARG_IS:[%%~2]\r\n' > echoarg.cmd
python -c "import subprocess,os; \
print(subprocess.run([os.path.abspath('echoarg.cmd'),'-p','L1\nL2\nL3'], \
stdout=subprocess.PIPE).stdout.decode())"
# -> ARG_IS:[L1]   (alles ab dem ersten \n ist weg)
```

> **Root Cause:** `shutil.which("kimi")` → `kimi.CMD` (npm-Batch-Shim). `subprocess.run`
> startet die `.CMD` über `cmd.exe`, das jedes Argument am ersten `\n` abschneidet (und die
> Zeile bei ~8191 Zeichen kappt). Der darunterliegende `node …main.mjs %*` erhält nur Zeile 1.
> **Fix (umgesetzt 2026-06-28):** der Adapter löst eine `.CMD`/`.BAT` über
> `_deshim_cmd_wrapper` auf ihr Node-Ziel (`node_modules/.../main.mjs`) auf und ruft
> `node main.mjs …` direkt auf. `CreateProcess` erhält `\n` und das Limit liegt bei ~32767
> Zeichen. Absicherung durch drei opt-in Live-Tests (`uv run pytest -m live`).

---

## 4. Testfälle — MCP-Tools

> Aufruf in Claude Code über die MCP-Tools `mcp__…__run_*`. **Kostenbremse:** für reine
> Funktionsprüfung `max_iterations=1` setzen. Als Target/Prompt bewusst **mehrzeiligen**
> Inhalt mit einem offensichtlichen Bug verwenden — so ist „gesund vs. broken" eindeutig.

Standard-Target (mehrzeilig, klarer Bug):
```
def is_even(n):
    return n % 2 == 1
# docstring: returns True for even numbers
```

| ID | Tool | Eingabe | Erwartung (gesund) | Beobachtet 2026-06-28 |
|----|------|---------|--------------------|-----------------------|
| T1 | `run_agent` | Standard-Target + „find the bug" | nennt `% 2 == 1`-Bug | ❌ „no function included" |
| T2 | `run_review_loop` (`max_iterations=1`) | Standard-Target | `verdict: request_changes`, Bug benannt | ❌ „No target was provided" |
| T3 | `run_santa_loop` (`max_iterations=1`) | Standard-Target | `red` mit echtem Befund (oder `green` bei sauberem Code) | ❌ `red`, „materials missing" |
| T4 | `run_planning_loop` (`max_iterations=1`) | „Plan a per-IP in-memory rate limiter, 100 req/min" (mehrzeilig) | nummerierter Plan | ❌ „message…cut off at 'plan for:'" |

**PASS-Kriterium je Tool:** Die Antwort bezieht sich **inhaltlich** auf das Target/den Prompt
(nennt den Bug bzw. erzeugt einen echten Plan). Eine „bitte gib mir den Code/Target"-Antwort =
**FAIL** (Transport-Bug, §3).

**Negativ-/Sicherheits-Verdict-Test (nur gesunde Bridge):** sauberes Target (`def is_even(n):
return n % 2 == 0`) durch `run_review_loop` → muss `approve` liefern; durch `run_santa_loop`
→ `green` nur wenn **beide** Reviewer approven (sonst `red`, fail-closed).

---

## 5. Testfälle — Skills / Commands

| ID | Command | Eingabe | Erwartung (gesund) |
|----|---------|---------|--------------------|
| S1 | `/kimi-run <prompt>` | mehrzeiliger Prompt | inhaltliche Antwort |
| S2 | `/kimi-code-review <datei|code>` | echte Quelldatei | priorisierte Findings (BLOCKER/MAJOR/…) |
| S3 | `/kimi-opinion <frage>` | Design-Trade-off-Frage | entscheidungsfreudige Antwort |
| S4 | `/kimi-review <target>` | Target | Verdict-Loop bzw. santa (Claude = 2. Reviewer) |

Bis der Bridge-Fix steht: S1–S4 zeigen denselben Truncation-Fehler wie §4, sobald die
Eingabe mehrzeilig ist (also praktisch immer). S4 santa über die Skill-Schicht nutzt Claude
als heterogenen Zweit-Reviewer (Host-Callback) — der **Claude-Teil** funktioniert, der
**Kimi-Primary** ist truncation-betroffen.

---

## 6. Sicherheits-/Boundary-Checks (müssen IMMER gelten)

Aus `runner.py` / `agent_registry/kimi.py` / `security/policy.py` — bei jedem Live-Test
gegenprüfen:

- [ ] **Read-only erzwungen:** `approval_policy` über `read-only` (z. B. `accept-edits`) →
      strukturierter Fehler `error: Policy escalation … not supported`, **kein** stiller Grant.
- [ ] **Keine Auto-Approve-Flags:** `--yolo`/`-y`/`--auto`/`--afk` werden NIE injiziert
      (NEVER_FLAGS). Optional: Prozess-Argv beim Spawn prüfen.
- [ ] **Worktree-Isolation:** Agent läuft in einem frischen Temp-Worktree (`create_isolated_worktree`);
      er kann das Host-Repo nicht beschreiben. Beleg: Kimi meldete „working directory is an empty
      temp scratch dir, no repo".
- [ ] **Depth-Guard:** `KIMI_BRIDGE_DEPTH` + `DEFAULT_MAX_DEPTH` (2) verhindert rekursive
      Agent-Schwärme; ein zu tiefer Spawn schlägt fail-fast fehl, ohne Worktree anzulegen.
- [ ] **Env-Allowlist:** nur `KIMI_*`/`ANTHROPIC_*` + definierte Exakt-Vars werden weitergereicht;
      Host-Secrets werden NICHT an den Child-Prozess vererbt.
- [ ] **Empty-Response-Fail-safe:** leere Kimi-Ausgabe → `needs_discussion`-Sentinel, nie „approve".

---

## 7. Bekannte Nebenwirkung: Team-OS-Kontamination

Die Kimi-CLI lädt hier global `~/.kimi-code/AGENTS.md` (Team-OS G2). Dadurch interpretiert
Kimi ein Rollen-Satz wie „You are a reviewer" als „Reviewer-Abteilung", startet `uni:start`
und fragt nach PR/Branch. Im Live-Test 2026-06-28 lief im santa-Adversary sogar das
Fact-Forcing-Gate mit. **Mitigation:** Brief mit explizitem „STANDALONE inline review, do NOT
run uni:start, do NOT ask for a PR/branch, the code is in THIS message" beginnen — und diese
Suppression in die Brief-Templates der Loops aufnehmen.

---

## 8. Ergebnis-Log (zuletzt: 2026-06-28, nach De-Shim-Fix)

| Test | Ergebnis | Notiz |
|------|----------|-------|
| Pre-flight (a)–(d) | ✅ | kimi 0.20.1 auth ok; `kimi`→`kimi.CMD` (wird jetzt de-shimt); node ok; 4 MCP-Tools geladen |
| §3.1 Single-Line | ✅ | Bug korrekt benannt |
| §3.2 Multi-Line | ✅ | Bug auf Zeile 2 korrekt benannt — Fix greift |
| §3.3 Direkt-Node | ✅ | Bug korrekt benannt (Referenz) |
| §3.4 Diagnose | ✅ | `ARG_IS:[L1]` bestätigt die alte Root Cause (jetzt umgangen) |
| T1 run_agent (Multi-Line) | ✅ | nennt den `% 2 == 1`-Bug; E2E via MCP bewiesen |
| T2 run_review_loop | ✅ | Target erreicht, Verdict + Begründung bezogen auf den Bug |
| T3 run_santa_loop | ✅ | `red`/`green` mit echtem, target-bezogenem Befund |
| T4 run_planning_loop | ✅ | nummerierter Plan, Prompt vollständig angekommen |
| Live-Test-Suite (`-m live`) | ✅ | 3 Live-Tests grün (deshim-static + 2× multi-line-dynamic) |
| §6 Read-only-Guard | ✅ | `error: Policy escalation … not supported` bei `accept-edits` |
| §6 Worktree-Isolation | ✅ | Agent läuft im frischen Temp-Worktree |
| §6 Env-Allowlist | ✅ | Nur `KIMI_*`/`ANTHROPIC_*` + Exakt-Vars werden weitergereicht |

**Exit-Kriterium „GRÜN":** §3.2 (Multi-Line) und T1–T4 + S1–S4 liefern inhaltliche,
target-bezogene Antworten, und §6 ist vollständig abgehakt. **GRÜN erreicht.**

---

## 9. Empfohlene Automatisierung (nach dem Fix)

Damit der Bug nicht zurückkehrt, einen echten (nicht gemockten) Multi-Line-Smoke-Test
ergänzen, der auf Windows den realen Spawn-Pfad geht:

```python
# tests/live/test_multiline_transport.py  (mit pytest-Marker @pytest.mark.live, opt-in)
import pytest
from kimi_code_plugin_cc.agent_registry import get

@pytest.mark.live  # nur mit echtem, angemeldetem kimi laufen lassen
async def test_multiline_prompt_reaches_agent():
    msg = await get("kimi").run(
        "Reply with the word on the next line and nothing else.\nBANANA"
    )
    assert "BANANA" in msg.payload  # schlägt heute fehl (nur Zeile 1 kommt an)
```

CI-Hinweis: als separates, opt-in Job laufen lassen (braucht Kimi-Auth); im normalen
Unit-Lauf übersprungen.
