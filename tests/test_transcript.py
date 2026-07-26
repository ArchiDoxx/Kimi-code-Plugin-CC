"""Tests for the best-effort transcript recorder."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from kimi_code_plugin_cc.transcript import TranscriptRecorder

_META = {
    "loop": "santa",
    "agents": {"primary": "kimi", "adversary": "kimi"},
    "model": None,
    "max_iterations": 3,
}


def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TranscriptRecorder:
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.delenv("KIMI_TRANSCRIPTS", raising=False)
    recorder = TranscriptRecorder.start("santa", dict(_META))
    assert recorder is not None
    return recorder


def test_start_creates_run_dir_and_initial_run_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _start(tmp_path, monkeypatch)

    run_dir = Path(recorder.path)
    assert run_dir.is_dir()
    assert run_dir.parent == tmp_path

    data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["run_id"] == run_dir.name
    assert data["agents"] == {"primary": "kimi", "adversary": "kimi"}
    assert data["model"] is None
    assert data["max_iterations"] == 3
    assert data["rounds"] == []
    assert data["started"].endswith("Z")


def test_start_returns_none_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setenv("KIMI_TRANSCRIPTS", "0")

    assert TranscriptRecorder.start("santa", dict(_META)) is None


def test_start_returns_none_when_base_dir_is_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(blocker))
    monkeypatch.delenv("KIMI_TRANSCRIPTS", raising=False)

    assert TranscriptRecorder.start("santa", dict(_META)) is None


def test_record_round_writes_utf8_round_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _start(tmp_path, monkeypatch)

    recorder.record_round(
        index=1,
        role="primary",
        agent="kimi",
        model=None,
        prompt="check whether a -> b holds",
        response="the arrow → must survive cp1252",
        verdict="request_changes",
        duration_s=41.2,
    )

    round_file = Path(recorder.path) / "round-01-primary.md"
    text = round_file.read_text(encoding="utf-8")
    assert "check whether a -> b holds" in text
    assert "the arrow → must survive cp1252" in text
    assert "request_changes" in text


def test_run_json_after_two_rounds_and_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _start(tmp_path, monkeypatch)
    recorder.record_round(
        index=1,
        role="primary",
        agent="kimi",
        model=None,
        prompt="p1",
        response="r1",
        verdict="request_changes",
        duration_s=41.2,
    )
    recorder.record_round(
        index=2,
        role="adversary",
        agent="kimi",
        model=None,
        prompt="p2",
        response="r2",
        verdict="approve",
        duration_s=12.5,
    )
    recorder.finalize(final={"verdict": "red", "iterations": 2})

    data = json.loads((Path(recorder.path) / "run.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert [r["index"] for r in data["rounds"]] == [1, 2]
    assert data["rounds"][0]["file"] == "round-01-primary.md"
    assert data["rounds"][0]["verdict"] == "request_changes"
    assert data["rounds"][1]["file"] == "round-02-adversary.md"
    assert data["final"] == {"verdict": "red", "iterations": 2}
    for key in ("started", "finished"):
        assert data[key].endswith("Z")
        datetime.strptime(data[key], "%Y-%m-%dT%H:%M:%SZ")


def test_write_failure_disables_recorder_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.delenv("KIMI_TRANSCRIPTS", raising=False)

    original_write_text = Path.write_text
    calls = {"count": 0}

    def flaky_write_text(self: Path, *args: object, **kwargs: object) -> int:
        calls["count"] += 1
        if calls["count"] >= 2:
            raise OSError("simulated disk failure")
        return original_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    recorder = TranscriptRecorder.start("santa", dict(_META))
    assert recorder is not None  # first write (initial run.json) succeeded

    # The second write fails; nothing may escape and the recorder goes quiet.
    recorder.record_round(
        index=1,
        role="primary",
        agent="kimi",
        model=None,
        prompt="p",
        response="r",
        verdict=None,
        duration_s=None,
    )
    recorder.record_round(
        index=2,
        role="primary",
        agent="kimi",
        model=None,
        prompt="p",
        response="r",
        verdict=None,
        duration_s=None,
    )
    recorder.finalize(final={"verdict": "red"})

    # 1 initial write + exactly 1 failed attempt; later calls no-op.
    assert calls["count"] == 2


def test_start_prunes_oldest_runs_beyond_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setenv("KIMI_TRANSCRIPT_KEEP", "3")
    monkeypatch.delenv("KIMI_TRANSCRIPTS", raising=False)
    for i in range(5):  # KEEP + 2 pre-existing runs, oldest first
        (tmp_path / f"2026010{i}T000000Z-santa-abcde{i}").mkdir()

    recorder = TranscriptRecorder.start("santa", dict(_META))
    assert recorder is not None

    remaining = {p.name for p in tmp_path.iterdir()}
    assert "20260100T000000Z-santa-abcde0" not in remaining
    assert "20260101T000000Z-santa-abcde1" not in remaining
    assert "20260102T000000Z-santa-abcde2" in remaining
    assert Path(recorder.path).name in remaining


@pytest.mark.parametrize("garbage", ["banana", "-3"])
def test_garbage_keep_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, garbage: str
) -> None:
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setenv("KIMI_TRANSCRIPT_KEEP", garbage)
    monkeypatch.delenv("KIMI_TRANSCRIPTS", raising=False)
    for i in range(52):  # default KEEP (50) + 2
        (tmp_path / f"20260101T0000{i:02d}Z-santa-abcde{i}").mkdir()

    recorder = TranscriptRecorder.start("santa", dict(_META))
    assert recorder is not None

    # 50 kept + the one just created; the two oldest were pruned.
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 51
    assert not (tmp_path / "20260101T000000Z-santa-abcde0").exists()
    assert not (tmp_path / "20260101T000001Z-santa-abcde1").exists()


def test_run_id_is_lexically_sortable_and_wellformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _start(tmp_path, monkeypatch)

    run_id = Path(recorder.path).name
    assert re.fullmatch(r"\d{8}T\d{6}Z-[a-z]+-[0-9a-f]{6}", run_id)
    # The UTC timestamp prefix (16 chars incl. Z) is a parseable instant,
    # which is what makes lexical order chronological.
    prefix = run_id[:16]
    assert (
        datetime.strptime(prefix, "%Y%m%dT%H%M%SZ").strftime("%Y%m%dT%H%M%SZ") == prefix
    )
