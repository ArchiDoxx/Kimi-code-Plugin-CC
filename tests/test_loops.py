"""Tests for planning, review, and santa loops."""

from __future__ import annotations

from typing import Any

import pytest

from kimi_code_plugin_cc.agent_registry import register
from kimi_code_plugin_cc.agent_registry.base import AgentAdapter
from kimi_code_plugin_cc.loops.planning import PlanResult, planning_loop
from kimi_code_plugin_cc.loops.review import ReviewResult, ReviewVerdict, review_loop
from kimi_code_plugin_cc.loops.santa import SantaResult, SantaVerdict, santa_loop
from kimi_code_plugin_cc.protocol.messages import AgentMessage


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
    async def test_returns_plan_and_message(self) -> None:
        adapter = StubAdapter("plan-agent", ["plan v1", "plan v1", "plan v1"])
        register("plan-agent", adapter)
        result = await planning_loop("plan-agent", "task", max_iterations=3)
        assert isinstance(result, PlanResult)
        assert result.plan == "plan v1"
        assert result.iterations == 3
        assert result.status == "max_iterations"

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
