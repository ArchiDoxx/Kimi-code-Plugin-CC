"""Adversarial dual-review (santa) loop.

The loop converges to ``green`` only when two *independent* reviewers both
approve. v0.5's second reviewer is the host (Claude itself, via a callback the
skill layer wires up). When no host callback is supplied — e.g. when the loop
runs inside the MCP server, which cannot call back into the host — the second
reviewer is an **independent, adversarially-framed** run of the same (or a
different) external adapter. That keeps the loop callable from MCP while
remaining genuinely adversarial (different prompt, independent verdict).

Either way the loop is **fail-closed**: disagreement or non-convergence within
``max_iterations`` yields ``red``, never ``green``.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.protocol.messages import AgentMessage, increment_depth

from .review import ReviewResult, ReviewVerdict, _extract_verdict

DEFAULT_MAX_ITERATIONS = 3

# The host reviewer may be sync or async; both are awaited safely.
HostReviewer = Callable[[str, ReviewResult], ReviewResult | Awaitable[ReviewResult]]


class SantaVerdict(StrEnum):
    """Possible outcomes of the adversarial dual-review (santa) loop."""

    GREEN = "green"
    RED = "red"


class SantaResult(BaseModel):
    """Result returned by the santa loop."""

    verdict: SantaVerdict
    primary_review: ReviewResult
    secondary_review: ReviewResult
    iterations: int = Field(ge=1)
    explanation: str


def _build_initial_review_prompt(target: str) -> str:
    return (
        "Perform a thorough code review of the following target. "
        "Respond with a verdict (approve, request_changes, needs_discussion) "
        "and comments.\n\n"
        f"Target:\n{target}"
    )


def _build_revision_prompt(
    target: str,
    primary_review: ReviewResult,
    secondary_review: ReviewResult,
    iteration: int,
) -> str:
    return (
        f"Another reviewer disagrees with your review (iteration {iteration}).\n\n"
        f"Your previous review:\n{primary_review.review}\n\n"
        f"Their review:\n{secondary_review.review}\n\n"
        f"Please revise your review of the target:\n{target}"
    )


def _adversarial_prompt(target: str, primary_review: ReviewResult) -> str:
    return (
        "You are an INDEPENDENT adversarial reviewer. Another reviewer produced "
        "the review below. Do NOT inherit their conclusion — form your own.\n\n"
        f"Their review:\n{primary_review.review}\n\n"
        "Only reply with 'approve' if you find no real issue. Respond with a "
        "verdict (approve, request_changes, needs_discussion) and comments.\n\n"
        f"Target:\n{target}"
    )


def _create_message(
    bridge_id: str,
    depth: int,
    payload: str,
    metadata: dict[str, Any] | None,
) -> AgentMessage:
    return AgentMessage(
        bridge_id=bridge_id,
        depth=depth,
        approval_policy="read-only",
        payload=payload,
        metadata=metadata,
    )


def _advance_message(
    message: AgentMessage,
    new_payload: str,
    new_metadata: dict[str, Any] | None,
) -> AgentMessage:
    """Return a deeper copy of *message* with a new payload and metadata."""
    return increment_depth(message).model_copy(
        update={"payload": new_payload, "metadata": new_metadata},
    )


def _to_review_result(response: AgentMessage, iteration: int) -> ReviewResult:
    return ReviewResult(
        review=response.payload,
        verdict=_extract_verdict(response.payload),
        iterations=iteration,
        final_message=response,
    )


async def _secondary_review(
    target: str,
    primary_review: ReviewResult,
    iteration: int,
    adversary_agent: str,
    host_reviewer: HostReviewer | None,
) -> ReviewResult:
    """Obtain the independent second review.

    Prefers the host callback (truly heterogeneous: Claude reviewing itself).
    Falls back to an independent, adversarially-framed external adapter run so
    the loop stays callable when no host is wired (e.g. from the MCP server).
    The host callback may be sync or async; both are awaited correctly.
    """
    if host_reviewer is not None:
        result = host_reviewer(target, primary_review)
        if inspect.isawaitable(result):
            result = await result
        return result
    adapter = get(adversary_agent)
    response = await adapter.run(
        _adversarial_prompt(target, primary_review),
        context={"loop": "santa", "role": "adversary"},
    )
    return _to_review_result(response, iteration)


def _build_explanation(
    primary: ReviewResult | None,
    secondary: ReviewResult | None,
) -> str:
    if primary is None or secondary is None:
        return "No review produced."
    if primary.verdict != ReviewVerdict.APPROVE:
        return f"Primary reviewer did not approve ({primary.verdict.value})."
    return f"Secondary reviewer did not approve ({secondary.verdict.value})."


async def santa_loop(
    primary_agent: str,
    target: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    *,
    adversary_agent: str | None = None,
    host_reviewer: HostReviewer | None = None,
) -> SantaResult:
    """Run an adversarial dual-review loop.

    The primary agent reviews the target; an independent second reviewer
    (host callback if supplied, else an adversarially-framed external adapter)
    reviews the same target. The loop returns ``green`` only when both
    reviewers approve. On disagreement the primary gets up to *max_iterations*
    revision rounds, after which the result fail-closes to ``red``.

    Args:
        primary_agent: Registered adapter for the primary review.
        target: The artifact to review.
        max_iterations: Maximum rounds before fail-closed ``red``.
        adversary_agent: Optional adapter for the second reviewer. Defaults to
            ``primary_agent`` (an independent, adversarially-framed re-run of
            the same external agent). Pass a different agent for a
            cross-model second opinion.
        host_reviewer: Optional ``(target, primary_review) -> ReviewResult``
            callback (sync or async). When provided it is used as the
            (heterogeneous) second reviewer instead of an external adapter —
            this is how the skill layer wires Claude itself as reviewer #2.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    resolved_adversary = adversary_agent or primary_agent
    adapter = get(primary_agent)
    bridge_id = str(uuid.uuid4())

    message = _create_message(
        bridge_id=bridge_id,
        depth=0,
        payload=_build_initial_review_prompt(target),
        metadata={"loop": "santa", "max_iterations": max_iterations},
    )

    last_primary: ReviewResult | None = None
    last_secondary: ReviewResult | None = None

    for iteration in range(1, max_iterations + 1):
        response = await adapter.run(
            message.payload,
            context={"message": message.model_dump()},
        )
        primary_review = _to_review_result(response, iteration)
        last_primary = primary_review

        secondary_review = await _secondary_review(
            target, primary_review, iteration, resolved_adversary, host_reviewer
        )
        last_secondary = secondary_review

        if (
            primary_review.verdict == ReviewVerdict.APPROVE
            and secondary_review.verdict == ReviewVerdict.APPROVE
        ):
            return SantaResult(
                verdict=SantaVerdict.GREEN,
                primary_review=primary_review,
                secondary_review=secondary_review,
                iterations=iteration,
                explanation="Both reviewers approved.",
            )

        if iteration == max_iterations:
            break

        message = _advance_message(
            response,
            _build_revision_prompt(target, primary_review, secondary_review, iteration),
            {"loop": "santa", "iteration": iteration},
        )

    explanation = _build_explanation(last_primary, last_secondary)
    return SantaResult(
        verdict=SantaVerdict.RED,
        primary_review=last_primary,
        secondary_review=last_secondary,
        iterations=max_iterations,
        explanation=explanation,
    )
