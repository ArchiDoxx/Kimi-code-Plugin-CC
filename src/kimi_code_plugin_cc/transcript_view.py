"""Read-only viewer for the transcripts written by :mod:`transcript`.

The recorder turns every loop run into a directory (``run.json`` plus one
``round-NN-<role>.md`` per exchange); this module turns those directories back
into something readable on a terminal. It is the read half of the same
subsystem and holds itself to three rules:

- **Strictly read-only.** Nothing here creates, moves, touches or deletes a
  file. Reading a transcript must be safe while a loop is still writing one.
- **Garbage in, report out.** Crashed and half-written runs are the expected
  customers, so a corrupt ``run.json``, a round file that ``run.json``
  promises but disk does not have, or foreign content in a shared base
  directory all resolve to a message or a marked table row - never a
  traceback. Only :class:`TranscriptViewError` leaves this module, and the CLI
  turns it into one line plus exit code 1.
- **Console-safe.** Transcripts hold arbitrary UTF-8 while a Windows console
  often runs a legacy code page, where printing a character it cannot encode
  raises ``UnicodeEncodeError``. :func:`emit` degrades those characters
  instead of losing the output.

Recording can be off (``KIMI_TRANSCRIPTS=0``) while old transcripts are still
on disk, so the viewer deliberately ignores that switch and only honours
``KIMI_TRANSCRIPT_DIR`` - through :mod:`transcript`'s own resolution, so the
two halves can never disagree about where transcripts live.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from kimi_code_plugin_cc.transcript import _RUN_ID_RE, _base_dir

DEFAULT_LIMIT = 20
INCOMPLETE = "(incomplete)"
UNREADABLE = "(unreadable)"
UNKNOWN = "(unknown)"

# Round files as the recorder names them (``round-01-primary.md``). Applied to
# the file names run.json claims, so a corrupt or foreign summary can never
# send the viewer at a path outside the run directory.
_ROUND_FILE_RE = re.compile(r"round-\d{2,}-[a-z]+\.md")
# Within one round index, show the roles in the order the loops produce them
# rather than alphabetically (santa writes primary first, then adversary).
_ROLE_ORDER = ("primary", "adversary", "review", "plan")
# Keys the loops use for their outcome, most specific first.
_FINAL_KEYS = ("verdict", "status", "error")


class TranscriptViewError(Exception):
    """A user-facing viewer problem: unknown or ambiguous id, missing round.

    Raised instead of returned so the CLI has exactly one place that turns a
    viewer problem into a message plus exit code 1, and no path can reach the
    user as a traceback.
    """


@dataclass(frozen=True)
class RunSummary:
    """One row of ``transcripts list``."""

    run_id: str
    loop: str
    started: str
    final: str
    rounds: int


def list_runs(limit: int = DEFAULT_LIMIT) -> list[RunSummary]:
    """Summarize the newest *limit* runs, newest first.

    Only the runs actually returned are parsed, so a base directory with
    hundreds of runs costs the same as one with *limit*. A non-positive
    *limit* yields an empty list.
    """
    if limit <= 0:
        return []
    return [_summarize(run_dir) for run_dir in _run_dirs()[:limit]]


def resolve_run_id(prefix: str) -> Path:
    """Return the run directory for a full run id or a unique prefix.

    *prefix* is only ever matched against directory names discovered under the
    base directory - it is never joined into a path, so a value like ``..``
    resolves to nothing instead of escaping the base directory. An exact match
    wins over prefix matching, so a full run id stays unambiguous even when it
    is the prefix of a newer one.
    """
    run_dirs = _run_dirs()
    for run_dir in run_dirs:
        if run_dir.name == prefix:
            return run_dir
    matches = [run_dir for run_dir in run_dirs if run_dir.name.startswith(prefix)]
    if not matches:
        raise TranscriptViewError(
            f"no transcript run matches '{prefix}' in {_base_dir()}\n"
            "run 'kimi-code-plugin transcripts list' to see the recorded runs"
        )
    if len(matches) > 1:
        candidates = "\n".join(f"  {run_dir.name}" for run_dir in matches)
        raise TranscriptViewError(
            f"'{prefix}' matches {len(matches)} runs:\n{candidates}\n"
            "use a longer prefix"
        )
    return matches[0]


def render_run_list(runs: list[RunSummary]) -> str:
    """Render *runs* as a table, or say so when there is nothing to show."""
    if not runs:
        return f"No transcripts recorded in {_base_dir()}."
    rows = [
        [run.run_id, run.loop, run.final, str(run.rounds), run.started] for run in runs
    ]
    headers = ["RUN ID", "LOOP", "FINAL", "ROUNDS", "STARTED"]
    return _render_table(headers, rows)


def render_run(run_dir: Path) -> str:
    """Render one run: summary fields plus a table of its recorded rounds."""
    data = _read_run_json(run_dir)
    if data is None:
        return _render_unreadable_run(run_dir)
    fields = [
        ("run", str(data.get("run_id") or run_dir.name)),
        ("loop", str(data.get("loop") or _loop_from_run_id(run_dir.name))),
        ("agents", _agents_label(data)),
        ("model", str(data.get("model") or "(default)")),
        ("started", str(data.get("started") or UNKNOWN)),
        ("finished", str(data.get("finished") or INCOMPLETE)),
        ("final", _final_label(data)),
        ("path", str(run_dir)),
    ]
    width = max(len(name) for name, _ in fields) + 1
    summary = "\n".join(f"{name + ':':<{width}} {value}" for name, value in fields)
    return f"{summary}\n\n{_render_rounds_table(run_dir, data)}"


def render_round(run_dir: Path, index: int) -> str:
    """Return the content of every round file recorded under *index*.

    Santa records two roles per index, so more than one file is normal. Each
    file gets a one-line banner naming it - otherwise two concatenated rounds
    have no visible boundary - and the content follows verbatim.
    """
    files = _round_files(run_dir, index)
    if not files:
        raise TranscriptViewError(
            f"run {run_dir.name} has no round {index}\n"
            f"run 'kimi-code-plugin transcripts show {run_dir.name}' "
            "to see the recorded rounds"
        )
    blocks = []
    for path in files:
        try:
            # errors="replace": a truncated round file is still worth reading.
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            body = f"(unreadable: {exc})"
        blocks.append(f"--- {path.name} ---\n{body.rstrip()}")
    return "\n\n".join(blocks)


def emit(text: str, stream: TextIO | None = None) -> None:
    """Print *text*, surviving a console that cannot encode all of it.

    Transcripts hold whatever the reviewed code and the agent produced, so a
    single unicode arrow in a review is enough to make ``print`` raise
    ``UnicodeEncodeError`` on a cp1252 console - the failure mode this viewer
    exists to avoid. Reconfiguring the stream to ``errors="replace"`` fixes it
    where supported; the encode/decode fallback covers streams that cannot be
    reconfigured. Losing a character beats losing the output.
    """
    target = sys.stdout if stream is None else stream
    reconfigure = getattr(target, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(OSError, ValueError):
            reconfigure(errors="replace")
    try:
        print(text, file=target)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "ascii"
        safe = text.encode(encoding, "replace").decode(encoding, "replace")
        print(safe, file=target)


def _run_dirs() -> list[Path]:
    """Return the run directories under the base dir, newest first.

    Run ids start with a UTC timestamp, so reverse lexical order is
    newest-first. Only run-shaped directories count: the base directory is
    user-configurable and may hold unrelated content, which is exactly why the
    recorder's pruning uses the same filter. A missing base directory is not
    an error - nothing has been recorded yet.
    """
    try:
        entries = list(_base_dir().iterdir())
    except OSError:
        return []
    runs = [p for p in entries if _RUN_ID_RE.fullmatch(p.name) and p.is_dir()]
    return sorted(runs, key=lambda p: p.name, reverse=True)


def _read_run_json(run_dir: Path) -> dict[str, object] | None:
    """Parse ``run.json``, or return None when it cannot be read.

    ``UnicodeDecodeError`` is a ``ValueError``, so garbled bytes and invalid
    JSON take the same path. A non-object top level counts as unreadable
    rather than being special-cased downstream.
    """
    try:
        data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _summarize(run_dir: Path) -> RunSummary:
    """Build one list row from a run directory, readable or not."""
    data = _read_run_json(run_dir)
    if data is None:
        # A crashed run is the expected customer here: report what the run id
        # and the files on disk still tell us.
        return RunSummary(
            run_id=run_dir.name,
            loop=_loop_from_run_id(run_dir.name),
            started=UNKNOWN,
            final=UNREADABLE,
            rounds=len(_round_files(run_dir)),
        )
    rounds = data.get("rounds")
    return RunSummary(
        run_id=str(data.get("run_id") or run_dir.name),
        loop=str(data.get("loop") or _loop_from_run_id(run_dir.name)),
        started=str(data.get("started") or UNKNOWN),
        final=_final_label(data),
        rounds=len(rounds) if isinstance(rounds, list) else len(_round_files(run_dir)),
    )


def _loop_from_run_id(run_id: str) -> str:
    """Recover the loop name from a run id (``<utc>-<loop>-<hex>``).

    The id shape is guaranteed for everything :func:`_run_dirs` returns, which
    makes this the one field a corrupt ``run.json`` cannot take away.
    """
    parts = run_id.split("-")
    return parts[1] if len(parts) == 3 else UNKNOWN


def _final_label(data: dict[str, object]) -> str:
    """Describe a run's outcome in one word where possible.

    The loops write ``{"verdict": ...}``, ``{"status": ...}`` or
    ``{"error": ...}``; a run that never finalized has no ``final`` key at all
    (or an empty one, when it crashed before producing an outcome).
    """
    final = data.get("final")
    if not final:
        return INCOMPLETE
    if not isinstance(final, dict):
        return str(final)
    for key in _FINAL_KEYS:
        value = final.get(key)
        if isinstance(value, str) and value:
            return value
    return ", ".join(f"{key}={value}" for key, value in final.items())


def _agents_label(data: dict[str, object]) -> str:
    """Render the run's agents: a mapping for santa, a single name otherwise."""
    agents = data.get("agents")
    if isinstance(agents, dict) and agents:
        return ", ".join(f"{role}={name}" for role, name in agents.items())
    agent = data.get("agent")
    return str(agent) if agent else UNKNOWN


def _round_files(run_dir: Path, index: int | None = None) -> list[Path]:
    """Return the round files of a run (all of them, or those of one index)."""
    pattern = "round-*.md" if index is None else f"round-{index:02d}-*.md"
    try:
        files = [path for path in run_dir.glob(pattern) if path.is_file()]
    except OSError:
        return []
    return sorted(files, key=_round_sort_key)


def _round_sort_key(path: Path) -> tuple[int, int, str]:
    """Sort round files by index, then by the loops' own role order."""
    parts = path.stem.split("-", 2)
    index = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else 0
    role = parts[2] if len(parts) == 3 else ""
    order = _ROLE_ORDER.index(role) if role in _ROLE_ORDER else len(_ROLE_ORDER)
    return (index, order, path.name)


def _render_rounds_table(run_dir: Path, data: dict[str, object]) -> str:
    """Render the rounds ``run.json`` recorded, marking what disk lacks."""
    recorded = data.get("rounds")
    if not isinstance(recorded, list) or not recorded:
        return "No rounds recorded."
    rows = []
    for item in recorded:
        entry: dict[str, object] = item if isinstance(item, dict) else {}
        rows.append(
            [
                str(entry.get("index", UNKNOWN)),
                str(entry.get("role") or UNKNOWN),
                str(entry.get("agent") or UNKNOWN),
                str(entry.get("verdict") or "(none)"),
                _duration_label(entry.get("duration_s")),
                _file_label(run_dir, entry.get("file")),
            ]
        )
    headers = ["ROUND", "ROLE", "AGENT", "VERDICT", "DURATION", "FILE"]
    return _render_table(headers, rows)


def _file_label(run_dir: Path, filename: object) -> str:
    """Name a round file, marking one that ``run.json`` promised but lacks.

    A run interrupted between its round file and its ``run.json`` update is
    normal, and so is a manually pruned directory; both must read as a marked
    row rather than as a working pointer to nothing.
    """
    if not isinstance(filename, str) or not _ROUND_FILE_RE.fullmatch(filename):
        return f"{filename} (unexpected name)" if filename else UNKNOWN
    return filename if (run_dir / filename).is_file() else f"{filename} (missing)"


def _duration_label(value: object) -> str:
    """Format a recorded duration; the recorder allows it to be absent."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return UNKNOWN
    return f"{float(value):.1f}s"


def _render_unreadable_run(run_dir: Path) -> str:
    """Report a run whose ``run.json`` is missing or corrupt.

    The round files are the substance of a transcript, so an unreadable
    summary is a degraded view, not a dead end.
    """
    files = _round_files(run_dir)
    listing = "\n".join(f"  {path.name}" for path in files) or "  (none)"
    return (
        f"run:  {run_dir.name}\n"
        f"path: {run_dir}\n"
        "\n"
        "run.json is missing or unreadable - this run was probably interrupted.\n"
        f"round files on disk:\n{listing}"
    )


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a plain-text table with columns wide enough for their content."""
    widths = [len(header) for header in headers]
    for row in rows:
        for column, cell in enumerate(row):
            widths[column] = max(widths[column], len(cell))
    lines = [
        "  ".join(
            cell.ljust(widths[column]) for column, cell in enumerate(row)
        ).rstrip()
        for row in [headers, *rows]
    ]
    return "\n".join(lines)
