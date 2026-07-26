"""Tests for planning, review, and santa loops."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from kimi_code_plugin_cc.agent_registry import register
from kimi_code_plugin_cc.agent_registry.base import AgentAdapter
from kimi_code_plugin_cc.bridge.runner import RunResult
from kimi_code_plugin_cc.loops.planning import PlanResult, planning_loop
from kimi_code_plugin_cc.loops.prompts import STANDALONE_PREAMBLE
from kimi_code_plugin_cc.loops.review import (
    ReviewResult,
    ReviewVerdict,
    extract_verdict,
    review_loop,
)
from kimi_code_plugin_cc.loops.santa import SantaResult, SantaVerdict, santa_loop
from kimi_code_plugin_cc.protocol.messages import AgentMessage

CODEX_MODULE = "kimi_code_plugin_cc.agent_registry.codex"


class StubAdapter(AgentAdapter):
    """Test adapter that returns programmed responses."""

    def __init__(self, name: str, responses: list[str]) -> None:
        self._name = name
        self._responses = responses
        self._calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        self._calls.append((prompt, context))
        index = min(len(self._calls) - 1, len(self._responses) - 1)
        return AgentMessage(
            bridge_id="stub",
            depth=0,
            payload=self._responses[index],
            metadata={},
        )


class TestPlanningLoop:
    async def test_converges_and_reports_complete(self) -> None:
        # Identical refinement output means the plan converged: stop early and
        # report status="complete" (previously this status was unreachable).
        adapter = StubAdapter("plan-agent", ["plan v1", "plan v1", "plan v1"])
        register("plan-agent", adapter)
        result = await planning_loop("plan-agent", "task", max_iterations=3)
        assert isinstance(result, PlanResult)
        assert result.plan == "plan v1"
        assert result.iterations == 2
        assert result.status == "complete"

    async def test_runs_multiple_iterations(self) -> None:
        adapter = StubAdapter("plan-agent-2", ["plan a", "plan b", "plan c"])
        register("plan-agent-2", adapter)
        result = await planning_loop("plan-agent-2", "task", max_iterations=3)
        assert result.plan == "plan c"
        assert result.iterations == 3
        assert result.status == "max_iterations"

    async def test_invalid_max_iterations(self) -> None:
        with pytest.raises(ValueError):
            await planning_loop("plan-agent", "task", max_iterations=0)


class TestReviewLoop:
    async def test_stops_early_on_approve(self) -> None:
        adapter = StubAdapter(
            "review-agent", ["approve looks good", "request_changes bad"]
        )
        register("review-agent", adapter)
        result = await review_loop("review-agent", "src/x.py", max_iterations=3)
        assert isinstance(result, ReviewResult)
        assert result.verdict == ReviewVerdict.APPROVE
        assert result.iterations == 1

    async def test_runs_up_to_max_iterations(self) -> None:
        adapter = StubAdapter(
            "review-agent-2",
            [
                "needs_discussion caution",
                "needs_discussion still",
                "request_changes fail",
            ],
        )
        register("review-agent-2", adapter)
        result = await review_loop("review-agent-2", "src/y.py", max_iterations=3)
        assert result.verdict == ReviewVerdict.REQUEST_CHANGES
        assert result.iterations == 3

    async def test_invalid_max_iterations(self) -> None:
        with pytest.raises(ValueError):
            await review_loop("review-agent", "target", max_iterations=0)


class TestSantaLoop:
    async def test_green_when_both_agree(self) -> None:
        adapter = StubAdapter("santa-primary", ["approve perfect"])
        register("santa-primary", adapter)
        # Same stub acts as the independent adversary so no real CLI is spawned.
        result = await santa_loop(
            "santa-primary",
            "src/z.py",
            max_iterations=3,
            adversary_agent="santa-primary",
        )
        assert isinstance(result, SantaResult)
        assert result.verdict == SantaVerdict.GREEN
        assert result.iterations == 1
        assert "approved" in result.explanation

    async def test_red_on_host_disagreement(self) -> None:
        adapter = StubAdapter("santa-primary-dis", ["approve perfect", "approve still"])
        register("santa-primary-dis", adapter)

        def host_disagrees(_target: str, _primary: ReviewResult) -> ReviewResult:
            return ReviewResult(
                review="host rejects",
                verdict=ReviewVerdict.REQUEST_CHANGES,
                iterations=1,
                final_message=AgentMessage(
                    bridge_id="host",
                    payload="host rejects",
                ),
            )

        result = await santa_loop(
            "santa-primary-dis",
            "src/z.py",
            max_iterations=2,
            host_reviewer=host_disagrees,
        )
        assert result.verdict == SantaVerdict.RED
        assert result.iterations == 2

    async def test_red_with_async_host_reviewer(self) -> None:
        # The host callback may be a coroutine; the loop must await it.
        adapter = StubAdapter("santa-async-host", ["approve perfect", "approve still"])
        register("santa-async-host", adapter)

        async def host_disagrees(_target: str, _primary: ReviewResult) -> ReviewResult:
            return ReviewResult(
                review="async host rejects",
                verdict=ReviewVerdict.REQUEST_CHANGES,
                iterations=1,
                final_message=AgentMessage(bridge_id="host", payload="rejects"),
            )

        result = await santa_loop(
            "santa-async-host",
            "src/z.py",
            max_iterations=1,
            host_reviewer=host_disagrees,
        )
        assert result.verdict == SantaVerdict.RED

    async def test_red_when_external_adversary_disagrees(self) -> None:
        # Primary approves but the independent adversary requests changes.
        adapter = StubAdapter(
            "santa-adv", ["approve perfect", "request_changes real bug"]
        )
        register("santa-adv", adapter)
        result = await santa_loop(
            "santa-adv", "src/sec.py", max_iterations=2, adversary_agent="santa-adv"
        )
        assert result.verdict == SantaVerdict.RED
        assert result.iterations == 2
        assert "did not approve" in result.explanation

    async def test_invalid_max_iterations(self) -> None:
        with pytest.raises(ValueError):
            await santa_loop("santa-primary", "target", max_iterations=0)


class DepthRecordingStub(AgentAdapter):
    """Adapter that records the depth carried in the loop context per call.

    Loop iteration is not recursion (ADR-003): successive refinement rounds
    must NOT consume the recursion-depth budget. This stub captures the depth
    the loop forwards so a test can assert it stays constant across iterations.
    """

    def __init__(self, name: str, payload: str) -> None:
        self._name = name
        self._payload = payload
        self.observed_depths: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        # Reads the canonical top-level contract, same as the real adapter.
        self.observed_depths.append(int(context.get("depth", 0)))
        # Vary the payload per call so the planning loop does not converge early;
        # the verdict keyword is preserved and never becomes an approval.
        payload = f"{self._payload} (round {len(self.observed_depths)})"
        return AgentMessage(bridge_id="depth-stub", depth=0, payload=payload)


class ContractRecordingStub(AgentAdapter):
    """Records the full call context so a test can assert the loops forward the
    canonical top-level contract (bridge_id, depth, approval_policy) the real
    adapter reads — not the old nested ``{"message": ...}`` shape that silently
    dropped every field to its default."""

    def __init__(self, name: str, payload: str) -> None:
        self._name = name
        self._payload = payload
        self.contexts: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        self.contexts.append(context)
        return AgentMessage(bridge_id="contract-stub", depth=0, payload=self._payload)


class TestVerdictExtraction:
    """The verdict parser must be fail-closed: it must never read a negated or
    incidental mention of 'approve' as an approval, and disagreements must win."""

    def test_explicit_approve_still_matches(self) -> None:
        assert extract_verdict("Verdict: approve, looks good") == ReviewVerdict.APPROVE

    def test_negated_approve_does_not_approve(self) -> None:
        # Regression: the old substring scan matched "approve" first and returned
        # APPROVE here, flipping a fail-closed loop toward green.
        assert (
            extract_verdict("I do not approve; request_changes needed for safety")
            == ReviewVerdict.REQUEST_CHANGES
        )

    def test_approval_word_is_not_approve(self) -> None:
        # "approval" contains the substring "approve" — a word-boundary scan must
        # not treat it as a verdict.
        assert (
            extract_verdict("approval workflow looks fine")
            == ReviewVerdict.NEEDS_DISCUSSION
        )

    def test_disagreement_wins_over_approve(self) -> None:
        assert (
            extract_verdict("approve overall, but request_changes for the edge case")
            == ReviewVerdict.REQUEST_CHANGES
        )

    def test_needs_discussion_matched(self) -> None:
        assert extract_verdict("needs_discussion: open questions remain") == (
            ReviewVerdict.NEEDS_DISCUSSION
        )

    def test_unknown_text_defaults_to_needs_discussion(self) -> None:
        assert extract_verdict("the cake is a lie") == ReviewVerdict.NEEDS_DISCUSSION

    def test_case_insensitive(self) -> None:
        assert extract_verdict("REQUEST_CHANGES") == ReviewVerdict.REQUEST_CHANGES


class TestLoopDepthConstancy:
    """Loop iterations are refinement rounds, not recursion. The depth forwarded
    into each adapter call must stay constant so the recursion guard (ADR-003)
    is reserved for genuine nested delegation, not consumed by the loop itself."""

    async def test_review_loop_keeps_depth_constant(self) -> None:
        stub = DepthRecordingStub("depth-review", "needs_discussion maybe")
        register("depth-review", stub)
        await review_loop("depth-review", "target", max_iterations=3)
        assert stub.observed_depths == [0, 0, 0]

    async def test_planning_loop_keeps_depth_constant(self) -> None:
        stub = DepthRecordingStub("depth-plan", "plan iter")
        register("depth-plan", stub)
        await planning_loop("depth-plan", "task", max_iterations=3)
        assert stub.observed_depths == [0, 0, 0]

    async def test_santa_loop_keeps_primary_depth_constant(self) -> None:
        primary_stub = DepthRecordingStub("depth-santa", "request_changes nope")
        register("depth-santa", primary_stub)
        # Use a separate adversary so the recording stub only sees primary calls.
        adversary_stub = StubAdapter("depth-santa-adv", ["request_changes nope"])
        register("depth-santa-adv", adversary_stub)
        await santa_loop(
            "depth-santa",
            "target",
            max_iterations=3,
            adversary_agent="depth-santa-adv",
        )
        # Three primary rounds, each at depth 0.
        assert primary_stub.observed_depths == [0, 0, 0]


class TestLoopAdapterContract:
    """Each loop must forward the canonical top-level context the real adapter
    reads. The old nested ``{"message": ...}`` shape made the real KimiCodeAdapter
    silently fall back to defaults (bridge_id "kimi", depth 0, read-only) — a
    contract mismatch masked because the test stubs read the nested shape too."""

    async def test_review_loop_forwards_top_level_context(self) -> None:
        stub = ContractRecordingStub("contract-review", "needs_discussion")
        register("contract-review", stub)
        await review_loop("contract-review", "target", max_iterations=1)
        ctx = stub.contexts[0]
        assert set(ctx) >= {"bridge_id", "depth", "approval_policy"}
        assert ctx["depth"] == 0
        assert ctx["approval_policy"] == "read-only"
        # bridge_id is the conversation UUID, not the adapter-name fallback.
        assert ctx["bridge_id"] not in ("", "kimi", "contract-review")

    async def test_planning_loop_forwards_top_level_context(self) -> None:
        stub = ContractRecordingStub("contract-plan", "a plan")
        register("contract-plan", stub)
        await planning_loop("contract-plan", "task", max_iterations=1)
        ctx = stub.contexts[0]
        assert set(ctx) >= {"bridge_id", "depth", "approval_policy"}

    async def test_santa_loop_forwards_top_level_context(self) -> None:
        stub = ContractRecordingStub("contract-santa", "request_changes")
        register("contract-santa", stub)
        await santa_loop(
            "contract-santa",
            "target",
            max_iterations=1,
            adversary_agent="contract-santa",
        )
        ctx = stub.contexts[0]
        assert set(ctx) >= {"bridge_id", "depth", "approval_policy"}

    async def test_review_loop_forwards_model(self) -> None:
        stub = ContractRecordingStub("model-review", "needs_discussion")
        register("model-review", stub)
        await review_loop("model-review", "target", max_iterations=1, model="glm-4.6")
        assert stub.contexts[0]["model"] == "glm-4.6"

    async def test_review_loop_without_model_omits_key(self) -> None:
        stub = ContractRecordingStub("model-review-none", "needs_discussion")
        register("model-review-none", stub)
        await review_loop("model-review-none", "target", max_iterations=1)
        assert "model" not in stub.contexts[0]

    async def test_planning_loop_forwards_model(self) -> None:
        stub = ContractRecordingStub("model-plan", "a plan")
        register("model-plan", stub)
        await planning_loop("model-plan", "task", max_iterations=1, model="glm-4.6")
        assert stub.contexts[0]["model"] == "glm-4.6"

    async def test_santa_loop_forwards_model_to_both_reviewers(self) -> None:
        stub = ContractRecordingStub("model-santa", "request_changes")
        register("model-santa", stub)
        await santa_loop(
            "model-santa",
            "target",
            max_iterations=1,
            adversary_agent="model-santa",
            model="glm-4.6",
        )
        # Primary review + adversarial second review, both on the same stub.
        assert len(stub.contexts) == 2
        assert all(ctx.get("model") == "glm-4.6" for ctx in stub.contexts)


class TestVerdictTolerance:
    """The parser tolerates spacing/inflection variants while staying fail-closed."""

    def test_request_changes_with_space(self) -> None:
        assert extract_verdict("please request changes here") == (
            ReviewVerdict.REQUEST_CHANGES
        )

    def test_needs_discussion_with_space(self) -> None:
        assert extract_verdict("this needs discussion first") == (
            ReviewVerdict.NEEDS_DISCUSSION
        )

    def test_approved_inflection_matches(self) -> None:
        assert extract_verdict("looks good, approved") == ReviewVerdict.APPROVE

    def test_approval_noun_still_not_approve(self) -> None:
        # Fail-closed guard must survive the more tolerant pattern.
        assert extract_verdict("the approval process is documented") == (
            ReviewVerdict.NEEDS_DISCUSSION
        )


class TestConflictingStructuredVerdicts:
    """Two disagreeing VERDICT lines are ambiguity, and ambiguity is not approval.

    The santa loop quotes the other reviewer's full text into the next prompt,
    so a reply can legitimately contain a second VERDICT line. Picking the first
    (or the last) match would silently adopt whichever one landed there.
    """

    def test_conflicting_lines_fail_closed(self) -> None:
        text = (
            "Their review said:\nVERDICT: approve\n"
            "I disagree.\nVERDICT: request_changes\n"
        )
        assert extract_verdict(text) == ReviewVerdict.NEEDS_DISCUSSION

    def test_quoted_approve_cannot_override_own_request_changes(self) -> None:
        text = "VERDICT: request_changes\n\nVERDICT: approve\n"
        assert extract_verdict(text) == ReviewVerdict.NEEDS_DISCUSSION

    def test_repeated_identical_verdict_is_honoured(self) -> None:
        # Unanimous repetition is not ambiguity.
        text = "VERDICT: approve\nsummary\nVERDICT: approve\n"
        assert extract_verdict(text) == ReviewVerdict.APPROVE

    def test_single_line_still_wins_over_free_text(self) -> None:
        text = "I would request changes normally, but:\nVERDICT: approve"
        assert extract_verdict(text) == ReviewVerdict.APPROVE


class TestPromptContract:
    """Every round must restate the verdict contract, not just the first one."""

    async def test_review_loop_repeats_contract_in_later_rounds(self) -> None:
        stub = StubAdapter("contract-review", ["no verdict here", "still nothing"])
        register("contract-review", stub)
        await review_loop("contract-review", "target", max_iterations=2)
        assert len(stub._calls) == 2
        for prompt, _ctx in stub._calls:
            assert "VERDICT:" in prompt

    async def test_review_prompts_carry_standalone_preamble(self) -> None:
        stub = StubAdapter("preamble-review", ["nothing", "nothing"])
        register("preamble-review", stub)
        await review_loop("preamble-review", "target", max_iterations=2)
        for prompt, _ctx in stub._calls:
            assert prompt.startswith(STANDALONE_PREAMBLE)

    async def test_santa_adversary_prompt_carries_contract(self) -> None:
        stub = StubAdapter("contract-santa", ["request_changes please"])
        register("contract-santa", stub)
        await santa_loop("contract-santa", "target", max_iterations=1)
        # Primary review + adversarial review.
        assert len(stub._calls) == 2
        for prompt, _ctx in stub._calls:
            assert "VERDICT:" in prompt
            assert prompt.startswith(STANDALONE_PREAMBLE)

    async def test_santa_revision_prompt_carries_contract(self) -> None:
        stub = StubAdapter("revision-santa", ["request_changes please"])
        register("revision-santa", stub)
        await santa_loop("revision-santa", "target", max_iterations=2)
        # Round 1 primary + adversary, round 2 primary + adversary.
        assert len(stub._calls) == 4
        revision_prompt = stub._calls[2][0]
        assert "VERDICT:" in revision_prompt
        assert "do not repeat or quote" in revision_prompt.lower()

    async def test_planning_prompts_carry_standalone_preamble(self) -> None:
        stub = StubAdapter("preamble-plan", ["plan a", "plan b"])
        register("preamble-plan", stub)
        await planning_loop("preamble-plan", "task", max_iterations=2)
        for prompt, _ctx in stub._calls:
            assert prompt.startswith(STANDALONE_PREAMBLE)


class CrashAdapter(StubAdapter):
    """Stub adapter that raises on a programmed call number (1-based)."""

    def __init__(self, name: str, responses: list[str], fail_on_call: int) -> None:
        super().__init__(name, responses)
        self._fail_on_call = fail_on_call

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        if len(self._calls) + 1 == self._fail_on_call:
            self._calls.append((prompt, context))
            raise RuntimeError("adapter exploded")
        return await super().run(prompt, context)


def _enable_transcripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point recording at *tmp_path* and undo the conftest-wide disable."""
    monkeypatch.setenv("KIMI_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.delenv("KIMI_TRANSCRIPTS", raising=False)


def _round_files(run_dir: str) -> list[str]:
    return sorted(p.name for p in Path(run_dir).glob("round-*.md"))


def _run_json(run_dir: str) -> dict[str, Any]:
    return json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))


class TestLoopTranscripts:
    """The loops persist one on-disk transcript per run when recording is on.

    Recording must be purely additive: identical verdicts/reviews whether the
    recorder is off, on, or broken, and a crashed run keeps its partial
    transcript on disk.
    """

    async def test_review_loop_records_every_round(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_transcripts(tmp_path, monkeypatch)
        adapter = StubAdapter(
            "t-review", ["needs_discussion one", "request_changes two"]
        )
        register("t-review", adapter)

        result = await review_loop("t-review", "src/x.py", max_iterations=2)

        assert result.transcript_dir is not None
        run_dir = Path(result.transcript_dir)
        assert run_dir.parent == tmp_path
        assert _round_files(result.transcript_dir) == [
            "round-01-review.md",
            "round-02-review.md",
        ]
        data = _run_json(result.transcript_dir)
        assert data["final"] == {"verdict": "request_changes", "iterations": 2}
        assert data["agent"] == "t-review"
        assert data["max_iterations"] == 2

    async def test_transcript_dir_is_none_when_recording_disabled(self) -> None:
        # The conftest fixture sets KIMI_TRANSCRIPTS=0 for this test.
        adapter = StubAdapter("t-review-off", ["approve ok"])
        register("t-review-off", adapter)
        result = await review_loop("t-review-off", "target", max_iterations=1)
        assert result.transcript_dir is None

    async def test_santa_loop_records_primary_and_adversary_rounds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_transcripts(tmp_path, monkeypatch)
        adapter = StubAdapter("t-santa", ["request_changes nope"])
        register("t-santa", adapter)

        result = await santa_loop(
            "t-santa", "src/z.py", max_iterations=2, adversary_agent="t-santa"
        )

        assert result.verdict == SantaVerdict.RED
        assert result.transcript_dir is not None
        # Only the top-level result carries the path, not the nested reviews.
        assert result.primary_review.transcript_dir is None
        assert result.secondary_review.transcript_dir is None
        assert _round_files(result.transcript_dir) == [
            "round-01-adversary.md",
            "round-01-primary.md",
            "round-02-adversary.md",
            "round-02-primary.md",
        ]
        data = _run_json(result.transcript_dir)
        assert data["final"] == {"verdict": "red", "iterations": 2}
        assert data["agents"] == {"primary": "t-santa", "adversary": "t-santa"}

    async def test_santa_loop_records_host_reviewer_as_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_transcripts(tmp_path, monkeypatch)
        adapter = StubAdapter("t-santa-host", ["approve perfect"])
        register("t-santa-host", adapter)

        def host_approves(_target: str, _primary: ReviewResult) -> ReviewResult:
            return ReviewResult(
                review="host approves",
                verdict=ReviewVerdict.APPROVE,
                iterations=1,
                final_message=AgentMessage(bridge_id="host", payload="host approves"),
            )

        result = await santa_loop(
            "t-santa-host", "src/z.py", max_iterations=1, host_reviewer=host_approves
        )

        assert result.verdict == SantaVerdict.GREEN
        assert result.transcript_dir is not None
        adversary_file = Path(result.transcript_dir) / "round-01-adversary.md"
        text = adversary_file.read_text(encoding="utf-8")
        assert "- agent: host" in text
        assert "[host callback]" in text
        assert "host approves" in text
        data = _run_json(result.transcript_dir)
        assert data["final"] == {"verdict": "green", "iterations": 1}

    async def test_crash_keeps_partial_transcript(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_transcripts(tmp_path, monkeypatch)
        adapter = CrashAdapter("t-crash", ["needs_discussion hmm"], fail_on_call=2)
        register("t-crash", adapter)

        # The exception propagates unchanged, same as without recording.
        with pytest.raises(RuntimeError, match="adapter exploded"):
            await review_loop("t-crash", "target", max_iterations=3)

        run_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        assert len(run_dirs) == 1
        # Round 1 was written before the crash; round 2 never happened.
        assert (run_dirs[0] / "round-01-review.md").exists()
        assert not (run_dirs[0] / "round-02-review.md").exists()
        data = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
        assert "adapter exploded" in data["final"]["error"]

    async def test_broken_recorder_does_not_change_the_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        responses = ["needs_discussion one", "request_changes two"]

        # Baseline: recording disabled (conftest default).
        adapter_off = StubAdapter("t-eq-off", list(responses))
        register("t-eq-off", adapter_off)
        disabled = await review_loop("t-eq-off", "target", max_iterations=2)
        assert disabled.transcript_dir is None

        # Recording on, but every write after the initial run.json fails.
        _enable_transcripts(tmp_path, monkeypatch)
        original_write_text = Path.write_text
        calls = {"count": 0}

        def flaky_write_text(self: Path, *args: object, **kwargs: object) -> int:
            calls["count"] += 1
            if calls["count"] >= 2:
                raise OSError("simulated disk failure")
            return original_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "write_text", flaky_write_text)

        adapter_broken = StubAdapter("t-eq-broken", list(responses))
        register("t-eq-broken", adapter_broken)
        broken = await review_loop("t-eq-broken", "target", max_iterations=2)

        # A recorder existed, so a path is reported, but the outcome is identical.
        assert broken.transcript_dir is not None
        assert broken.verdict == disabled.verdict
        assert broken.review == disabled.review
        assert broken.iterations == disabled.iterations

    async def test_planning_loop_records_plan_rounds_and_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_transcripts(tmp_path, monkeypatch)
        adapter = StubAdapter("t-plan", ["plan a", "plan b", "plan b"])
        register("t-plan", adapter)

        result = await planning_loop("t-plan", "task", max_iterations=3)

        assert result.status == "complete"
        assert result.iterations == 3
        assert result.transcript_dir is not None
        assert _round_files(result.transcript_dir) == [
            "round-01-plan.md",
            "round-02-plan.md",
            "round-03-plan.md",
        ]
        data = _run_json(result.transcript_dir)
        assert data["final"] == {"status": "complete", "iterations": 3}


class TestCodexThroughTheRegistry:
    """Invariant 4 / plan test 11: the loops reach codex via the registry only.

    These go through the real ``CodexAdapter`` (not a StubAdapter) with only the
    subprocess mocked, so they prove the whole seam the plugin claims is
    agent-agnostic: registry lookup, context translation, policy handling and
    payload extraction.
    """

    def _mocks(self):
        return (
            mock.patch(
                f"{CODEX_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ),
            mock.patch(f"{CODEX_MODULE}.shutil.which", return_value="/usr/bin/codex"),
            mock.patch(f"{CODEX_MODULE}.supports_flag", return_value=False),
        )

    @staticmethod
    def _writes(payload: str):
        async def _run(args: list[str], **kwargs: Any) -> RunResult:
            Path(args[args.index("-o") + 1]).write_text(payload, encoding="utf-8")
            return RunResult(
                returncode=0, stdout="", stderr="", args=list(args), env={}
            )

        return _run

    async def test_review_loop_runs_codex_and_parses_its_verdict(self) -> None:
        run_patch, which_patch, probe_patch = self._mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = self._writes("Looks correct.\nVERDICT: approve")
            result = await review_loop("codex", "def f(): pass", max_iterations=2)

        assert isinstance(result, ReviewResult)
        assert result.verdict is ReviewVerdict.APPROVE
        assert result.iterations == 1  # approval stops the loop early
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0][1] == "exec"

    async def test_review_loop_fails_closed_when_codex_produces_nothing(self) -> None:
        """A codex run with no final message must not become a silent approval."""
        run_patch, which_patch, probe_patch = self._mocks()

        async def _no_output(args: list[str], **kwargs: Any) -> RunResult:
            return RunResult(
                returncode=0, stdout="", stderr="", args=list(args), env={}
            )

        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _no_output
            with pytest.raises(RuntimeError, match="no final message"):
                await review_loop("codex", "target", max_iterations=1)
