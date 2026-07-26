"""Best-effort transcript persistence for the agent loops.

Each loop run (review, santa, plan, ...) gets its own directory holding a
small ``run.json`` summary plus one Markdown file per recorded exchange. The
recorder is deliberately fail-open: persistence must never crash a caller, so
every disk operation is best-effort. The first failed write disables the
recorder for the rest of the run, and :meth:`TranscriptRecorder.start` returns
``None`` instead of raising when recording is unavailable or turned off.

Configuration is read from the environment at call time (never at import):

- ``KIMI_TRANSCRIPTS`` - ``0``/``false``/``no``/``off`` disables recording.
- ``KIMI_TRANSCRIPT_DIR`` - base directory override (default
  ``~/.kimi-code-plugin-cc/transcripts``).
- ``KIMI_TRANSCRIPT_KEEP`` - newest N run directories kept (default 50);
  older runs are pruned at start, best-effort.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_ENABLED = "KIMI_TRANSCRIPTS"
_ENV_BASE_DIR = "KIMI_TRANSCRIPT_DIR"
_ENV_KEEP = "KIMI_TRANSCRIPT_KEEP"
DEFAULT_KEEP = 50
SCHEMA_VERSION = 1
_FALSEY = frozenset({"0", "false", "no", "off"})
# Shape of the run directories this module creates (see ``start``). Pruning
# must only ever delete directories that provably match it: the base dir is
# user-configurable, so anything else in there is not ours to remove.
_RUN_ID_RE = re.compile(r"\d{8}T\d{6}Z-[a-z]+-[0-9a-f]{6}")


def _utc_now() -> str:
    """Return the current UTC time as ISO-8601 with a ``Z`` suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _transcripts_enabled() -> bool:
    """Return True unless ``KIMI_TRANSCRIPTS`` is explicitly falsey."""
    return os.environ.get(_ENV_ENABLED, "1").strip().lower() not in _FALSEY


def _keep_count() -> int:
    """Return the configured retention, falling back to the default.

    A non-numeric or non-positive override is ignored rather than allowed to
    prune everything (fail-safe, same pattern as ``_max_prompt_chars``).
    """
    raw = os.environ.get(_ENV_KEEP)
    if raw is None:
        return DEFAULT_KEEP
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_KEEP
    return value if value > 0 else DEFAULT_KEEP


def _base_dir() -> Path:
    """Return the transcript base directory (env override or default)."""
    override = os.environ.get(_ENV_BASE_DIR)
    if override:
        return Path(override)
    return Path.home() / ".kimi-code-plugin-cc" / "transcripts"


def _prune(base: Path, keep: int) -> None:
    """Remove all but the *keep* newest run directories under *base*.

    Run ids start with a UTC timestamp, so lexical sort order is
    chronological. Only directories matching ``_RUN_ID_RE`` are considered:
    the base directory is user-configurable, so unrelated content in it must
    never be touched. Failures are ignored on purpose: pruning is
    housekeeping and must never affect the run being started.
    """
    try:
        run_dirs = sorted(
            p for p in base.iterdir() if p.is_dir() and _RUN_ID_RE.fullmatch(p.name)
        )
    except OSError:
        return
    for old in run_dirs[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def _render_round(
    *,
    index: int,
    role: str,
    agent: str,
    model: str | None,
    prompt: str,
    response: str,
    verdict: str | None,
    duration_s: float | None,
) -> str:
    """Render one recorded exchange as a Markdown document."""
    return (
        f"# round {index} - {role}\n\n"
        f"- agent: {agent}\n"
        f"- model: {model if model is not None else '(default)'}\n"
        f"- recorded: {_utc_now()}\n"
        f"- duration_s: {duration_s if duration_s is not None else '(unknown)'}\n"
        f"- verdict: {verdict if verdict is not None else '(none)'}\n"
        "\n"
        f"## Prompt\n\n{prompt}\n\n"
        f"## Response\n\n{response}\n"
    )


class TranscriptRecorder:
    """Persists one loop run to disk, best-effort and never raising.

    Created via :meth:`start`. After the first failed write the recorder
    disables itself and all further calls are no-ops.
    """

    def __init__(
        self, run_dir: Path, run_id: str, loop: str, meta: dict[str, object]
    ) -> None:
        self._run_dir = run_dir
        self._run_id = run_id
        self._loop = loop
        self._meta = dict(meta)
        self._started = _utc_now()
        self._finished: str | None = None
        self._final: dict[str, object] | None = None
        self._rounds: list[dict[str, object]] = []
        self._disabled = False

    @classmethod
    def start(cls, loop: str, meta: dict[str, object]) -> TranscriptRecorder | None:
        """Create the run directory and write an initial run.json.

        Returns None when disabled (KIMI_TRANSCRIPTS=0) or when the base
        directory cannot be created/written. NEVER raises.
        Also performs best-effort pruning of old runs (keep newest N).
        """
        if not _transcripts_enabled():
            return None
        try:
            base = _base_dir()
            base.mkdir(parents=True, exist_ok=True)
            _prune(base, _keep_count())
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"{timestamp}-{loop}-{secrets.token_hex(3)}"
            run_dir = base / run_id
            run_dir.mkdir()
            recorder = cls(run_dir, run_id, loop, meta)
            recorder._write_run_json()
        except Exception:
            logger.warning("transcript recording unavailable", exc_info=True)
            return None
        return recorder

    @property
    def path(self) -> str:
        """Absolute directory holding this run's transcript files."""
        return str(self._run_dir)

    def record_round(
        self,
        *,
        index: int,
        role: str,
        agent: str,
        model: str | None,
        prompt: str,
        response: str,
        verdict: str | None,
        duration_s: float | None,
    ) -> None:
        """Write one round file and refresh run.json (best-effort).

        ``index`` is the 1-based loop iteration, ``role`` one of
        ``"review" | "plan" | "primary" | "adversary"`` and ``agent`` the
        adapter name (``"host"`` for the host reviewer). Never raises; the
        first failure disables the recorder for the rest of the run.
        """
        if self._disabled:
            return
        filename = f"round-{index:02d}-{role}.md"
        try:
            (self._run_dir / filename).write_text(
                _render_round(
                    index=index,
                    role=role,
                    agent=agent,
                    model=model,
                    prompt=prompt,
                    response=response,
                    verdict=verdict,
                    duration_s=duration_s,
                ),
                encoding="utf-8",
            )
            self._rounds.append(
                {
                    "index": index,
                    "role": role,
                    "file": filename,
                    "agent": agent,
                    "verdict": verdict,
                    "duration_s": duration_s,
                }
            )
            self._write_run_json()
        except (OSError, UnicodeError):
            logger.warning(
                "transcript write failed; disabling recorder for this run",
                exc_info=True,
            )
            self._disabled = True

    def finalize(self, *, final: dict[str, object]) -> None:
        """Record the run outcome into run.json (best-effort)."""
        if self._disabled:
            return
        try:
            self._finished = _utc_now()
            self._final = dict(final)
            self._write_run_json()
        except (OSError, UnicodeError):
            logger.warning(
                "transcript write failed; disabling recorder for this run",
                exc_info=True,
            )
            self._disabled = True

    def _write_run_json(self) -> None:
        """Atomically rewrite the run summary (tmp file + os.replace).

        The ``meta`` mapping passed to :meth:`start` is copied in verbatim
        under its own keys; the recorder adds the bookkeeping fields, which
        win over meta keys on collision.
        """
        data: dict[str, object] = {
            **self._meta,
            "schema_version": SCHEMA_VERSION,
            "run_id": self._run_id,
            "loop": self._loop,
            "started": self._started,
            "rounds": list(self._rounds),
        }
        if self._finished is not None:
            data["finished"] = self._finished
        if self._final is not None:
            data["final"] = self._final
        tmp = self._run_dir / "run.json.tmp"
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self._run_dir / "run.json")
