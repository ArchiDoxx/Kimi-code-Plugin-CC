"""Tests for the read-only transcript viewer (``kimi-code-plugin transcripts``).

The viewer's customers are crashed and half-written runs, so most of these
tests feed it damaged input: a corrupt ``run.json``, a round file that
``run.json`` promises but disk does not have, foreign content in a shared base
directory. None of that may surface as a traceback, and none of it may write.
"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from kimi_code_plugin_cc.cli import main
from kimi_code_plugin_cc.transcript_view import list_runs, resolve_run_id

RUN_OLD = "20260726T101500Z-review-aaa111"
RUN_MID = "20260726T111500Z-santa-bbb222"
RUN_NEW = "20260726T121500Z-santa-ccc333"

_FINAL_RED: dict[str, object] = {"verdict": "red", "iterations": 2}
_SANTA_ROUNDS: tuple[tuple[int, str], ...] = ((1, "primary"), (1, "adversary"))


def _round_body(index: int, role: str, response: str) -> str:
    """Mimic the recorder's round file layout closely enough to read it back."""
    return (
        f"# round {index} - {role}\n\n"
        "- agent: kimi\n"
        "- model: (default)\n"
        "- duration_s: 41.2\n"
        "- verdict: request_changes\n\n"
        "## Prompt\n\nreview this\n\n"
        f"## Response\n\n{response}\n"
    )


def _make_run(
    base: Path,
    run_id: str,
    *,
    loop: str = "santa",
    started: str = "2026-07-26T11:15:00Z",
    finished: str | None = "2026-07-26T11:18:02Z",
    final: dict[str, object] | None = _FINAL_RED,
    rounds: Sequence[tuple[int, str]] = _SANTA_ROUNDS,
    response: str = "the review body",
    write_round_files: bool = True,
    corrupt_run_json: bool = False,
) -> Path:
    """Write one run directory the way the recorder would have written it."""
    run_dir = base / run_id
    run_dir.mkdir(parents=True)
    recorded: list[dict[str, object]] = []
    for index, role in rounds:
        filename = f"round-{index:02d}-{role}.md"
        if write_round_files:
            (run_dir / filename).write_text(
                _round_body(index, role, response), encoding="utf-8"
            )
        recorded.append(
            {
                "index": index,
                "role": role,
                "file": filename,
                "agent": "kimi",
                "verdict": "request_changes",
                "duration_s": 41.2,
            }
        )
    if corrupt_run_json:
        # Truncated/garbled bytes, not merely invalid JSON: this also exercises
        # the utf-8 decode path.
        (run_dir / "run.json").write_bytes(b'\xff\xfe{"run_id": "2026')
        return run_dir
    data: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "loop": loop,
        "agents": {"primary": "kimi", "adversary": "kimi"},
        "model": None,
        "max_iterations": 3,
        "started": started,
        "rounds": recorded,
    }
    if finished is not None:
        data["finished"] = finished
    if final is not None:
        data["final"] = final
    (run_dir / "run.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return run_dir


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An existing, empty transcript base directory."""
    root = tmp_path / "transcripts"
    root.mkdir()
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(root))
    return root


@pytest.fixture
def populated(base: Path) -> Path:
    """Three runs: one review run and two santa runs, oldest first."""
    _make_run(base, RUN_OLD, loop="review", rounds=((1, "review"),))
    _make_run(base, RUN_MID)
    _make_run(base, RUN_NEW)
    return base


def _tree(base: Path) -> dict[str, tuple[int, int]]:
    """Snapshot every file under *base* by size and mtime."""
    return {
        str(p.relative_to(base)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


class TestList:
    def test_missing_base_dir_is_a_friendly_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Nothing recorded yet is not an error, and the dir must not be created.
        missing = tmp_path / "never-used"
        monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(missing))

        assert main(["transcripts", "list"]) == 0
        assert "No transcripts" in capsys.readouterr().out
        assert not missing.exists()

    def test_empty_base_dir_is_a_friendly_zero(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["transcripts", "list"]) == 0
        out = capsys.readouterr().out
        assert "No transcripts" in out
        assert str(base) in out

    def test_lists_newest_first_and_respects_limit(
        self, populated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["transcripts", "list", "--limit", "2"]) == 0

        out = capsys.readouterr().out
        assert RUN_OLD not in out
        assert out.index(RUN_NEW) < out.index(RUN_MID)

    def test_list_runs_sorts_newest_first(self, populated: Path) -> None:
        assert [run.run_id for run in list_runs()] == [RUN_NEW, RUN_MID, RUN_OLD]

    def test_default_limit_caps_the_listing(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for minute in range(22):
            _make_run(base, f"20260726T10{minute:02d}00Z-santa-{minute:06x}")

        assert main(["transcripts", "list"]) == 0

        rows = [
            line for line in capsys.readouterr().out.splitlines() if "-santa-" in line
        ]
        assert len(rows) == 20

    def test_run_without_final_is_incomplete(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_run(base, RUN_MID, final=None, finished=None)

        assert main(["transcripts", "list"]) == 0
        assert "(incomplete)" in capsys.readouterr().out

    def test_corrupt_run_json_is_unreadable_not_a_crash(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_run(base, RUN_MID, corrupt_run_json=True)

        assert main(["transcripts", "list"]) == 0
        out = capsys.readouterr().out
        assert "(unreadable)" in out
        # The loop name is still recoverable from the run id itself.
        assert "santa" in out

    def test_foreign_content_in_the_base_dir_is_ignored(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The base dir is user-configurable and may be shared with other data.
        (base / "backup-2024").mkdir()
        (base / "0-aardvark").mkdir()
        (base / "loose-file.txt").write_text("not ours", encoding="utf-8")
        _make_run(base, RUN_MID)

        assert main(["transcripts", "list"]) == 0

        out = capsys.readouterr().out
        assert RUN_MID in out
        for foreign in ("backup-2024", "0-aardvark", "loose-file.txt"):
            assert foreign not in out

    def test_non_positive_limit_is_a_usage_error(self, populated: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["transcripts", "list", "--limit", "0"])
        assert exc.value.code == 2


class TestShow:
    def test_renders_summary_and_one_row_per_round(
        self, populated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["transcripts", "show", RUN_MID]) == 0

        out = capsys.readouterr().out
        assert RUN_MID in out
        assert "santa" in out
        assert "primary=kimi" in out
        assert "adversary=kimi" in out
        assert "2026-07-26T11:15:00Z" in out
        assert "red" in out
        assert "round-01-primary.md" in out
        assert "round-01-adversary.md" in out
        assert "41.2s" in out

    def test_resolves_a_unique_prefix(
        self, populated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["transcripts", "show", "20260726T1215"]) == 0
        assert RUN_NEW in capsys.readouterr().out

    def test_resolve_run_id_accepts_a_unique_prefix(self, populated: Path) -> None:
        assert resolve_run_id("20260726T1215").name == RUN_NEW

    def test_an_exact_id_wins_over_prefix_matching(self, base: Path) -> None:
        # A full run id must resolve even when it is the prefix of another one.
        short = "20260726T101500Z-santa-aaa111"
        _make_run(base, short)
        _make_run(base, "20260726T101500Z-santa-aaa1119")

        assert resolve_run_id(short).name == short

    def test_ambiguous_prefix_exits_one_and_names_candidates(
        self, populated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["transcripts", "show", "20260726T1"]) == 1

        err = capsys.readouterr().err
        assert RUN_OLD in err
        assert RUN_MID in err
        assert RUN_NEW in err

    def test_unknown_id_exits_one_and_names_the_base_dir(
        self, populated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["transcripts", "show", "19990101T000000Z-santa-abcdef"]) == 1
        assert str(populated) in capsys.readouterr().err

    def test_unreadable_run_json_reports_instead_of_raising(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_run(base, RUN_MID, corrupt_run_json=True)

        assert main(["transcripts", "show", RUN_MID]) == 0

        out = capsys.readouterr().out
        assert "unreadable" in out
        # The round files on disk are still the useful part of a broken run.
        assert "round-01-primary.md" in out

    def test_round_file_promised_by_run_json_but_absent_is_marked(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_run(base, RUN_MID, write_round_files=False)

        assert main(["transcripts", "show", RUN_MID]) == 0
        assert "(missing)" in capsys.readouterr().out


class TestShowRound:
    def test_prints_every_role_recorded_under_the_index(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Santa records two roles per index; both belong to `--round N`.
        _make_run(base, RUN_MID, response="verbatim body")

        assert main(["transcripts", "show", RUN_MID, "--round", "1"]) == 0

        out = capsys.readouterr().out
        assert "round-01-primary.md" in out
        assert "round-01-adversary.md" in out
        assert out.count("verbatim body") == 2
        assert "## Prompt" in out
        # Primary before adversary, not alphabetical.
        assert out.index("round-01-primary.md") < out.index("round-01-adversary.md")

    def test_missing_round_exits_one_without_a_traceback(
        self, populated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["transcripts", "show", RUN_MID, "--round", "9"]) == 1

        err = capsys.readouterr().err
        assert "round 9" in err
        assert "Traceback" not in err

    def test_arrow_survives_a_cp1252_stdout(
        self, base: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The documented trap: transcripts hold arbitrary UTF-8 while a Windows
        # console often runs a legacy code page.
        _make_run(base, RUN_MID, rounds=((1, "review"),), response="a → b")
        buffer = io.BytesIO()
        console = io.TextIOWrapper(buffer, encoding="cp1252", newline="")
        monkeypatch.setattr("sys.stdout", console)

        code = main(["transcripts", "show", RUN_MID, "--round", "1"])
        console.flush()

        assert code == 0
        text = buffer.getvalue().decode("cp1252")
        assert "round 1 - review" in text
        assert "a ? b" in text

    def test_arrow_survives_a_stream_that_cannot_be_reconfigured(
        self, base: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_run(base, RUN_MID, rounds=((1, "review"),), response="a → b")
        console = _StrictStream()
        monkeypatch.setattr("sys.stdout", console)

        assert main(["transcripts", "show", RUN_MID, "--round", "1"]) == 0
        assert "a ? b" in "".join(console.chunks)


class _StrictStream:
    """A text sink without ``reconfigure`` that rejects non-cp1252 characters."""

    encoding = "cp1252"

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> int:
        text.encode(self.encoding)  # raises like a real legacy console
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        return None


class TestReadOnly:
    def test_viewing_never_writes(
        self, populated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = _tree(populated)
        before_entries = sorted(p.name for p in populated.iterdir())

        assert main(["transcripts", "list"]) == 0
        assert main(["transcripts", "show", RUN_MID]) == 0
        assert main(["transcripts", "show", RUN_MID, "--round", "1"]) == 0
        assert main(["transcripts", "show", "nope"]) == 1
        capsys.readouterr()

        assert _tree(populated) == before
        assert sorted(p.name for p in populated.iterdir()) == before_entries


class TestCliSurface:
    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["transcripts", "list"], 0),
            (["transcripts", "list", "--limit", "1"], 0),
            (["transcripts", "show", RUN_MID], 0),
            (["transcripts", "show", "20260726T1215"], 0),
            (["transcripts", "show", RUN_MID, "--round", "1"], 0),
            (["transcripts", "show", "20260726T1"], 1),
            (["transcripts", "show", "19990101T000000Z-santa-abcdef"], 1),
            (["transcripts", "show", RUN_MID, "--round", "9"], 1),
        ],
    )
    def test_exit_codes(
        self,
        populated: Path,
        capsys: pytest.CaptureFixture[str],
        argv: list[str],
        expected: int,
    ) -> None:
        assert main(argv) == expected
        capsys.readouterr()

    def test_transcripts_without_subcommand_prints_its_help(
        self, base: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Pinned: a missing subcommand is a usage error (2), not a data error (1).
        assert main(["transcripts"]) == 2

        out = capsys.readouterr().out
        assert "list" in out
        assert "show" in out

    def test_top_level_help_mentions_transcripts(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main([]) == 0
        assert "transcripts" in capsys.readouterr().out
