from __future__ import annotations

import re
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.protocol.messages import AgentMessage, to_adapter_context

DEFAULT_MAX_ITERATIONS = 3


class ReviewVerdict(StrEnum):
    """Possible outcomes of a review loop."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    NEEDS_DISCUSSION = "needs_discussion"


class ReviewResult(BaseModel):
    """Result returned by the review loop."""

    review: str
    verdict: ReviewVerdict
    iterations: int = Field(ge=1)
    final_message: AgentMessage


def _build_initial_review_prompt(target: str) -> str:
    verdicts = ", ".join(choice.value for choice in ReviewVerdict)
    return (
        "Review the following target. Respond with a verdict "
        f"({verdicts}) and concise comments.\n\nTarget:\n{target}"
    )


def _build_refinement_prompt(target: str, previous_review: str, iteration: int) -> str:
    return (
        f"Refine your review (iteration {iteration}).\n\n"
        f"Previous review:\n{previous_review}\n\n"
        f"Target:\n{target}"
    )


# Verdict patterns tolerate the space- or underscore-separated spelling an
# agent may emit ("request_changes" or "request changes"). APPROVE also accepts
# "approved"/"approves" but NOT "approval" — that noun is not a verdict and must
# never read as one (fail-closed).
_REQUEST_CHANGES_RE = re.compile(r"\brequest[ _]changes\b")
_NEEDS_DISCUSSION_RE = re.compile(r"\bneeds[ _]discussion\b")
_APPROVE_RE = re.compile(r"\bapprove[ds]?\b")


def _extract_verdict(text: str) -> ReviewVerdict:
    lowered = text.lower()
    # Fail-closed: non-approve verdicts take precedence. APPROVE is accepted
    # only when no disagreement verdict appears anywhere in the text.
    if _REQUEST_CHANGES_RE.search(lowered):
        return ReviewVerdict.REQUEST_CHANGES
    if _NEEDS_DISCUSSION_RE.search(lowered):
        return ReviewVerdict.NEEDS_DISCUSSION
    if _APPROVE_RE.search(lowered):
        return ReviewVerdict.APPROVE
    return ReviewVerdict.NEEDS_DISCUSSION


def _create_review_message(
    bridge_id: str,
    depth: int,
    payload: str,
    metadata: dict | None,
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
    new_metadata: dict | None,
) -> AgentMessage:
    """Return a copy of *message* with a new payload and metadata.

    Loop iterations are refinement rounds, not recursion, so the depth is kept
    constant (ADR-003).
    """
    return message.model_copy(
        update={"payload": new_payload, "metadata": new_metadata},
    )


def _build_result(response: AgentMessage, iteration: int) -> ReviewResult:
    return ReviewResult(
        review=response.payload,
        verdict=_extract_verdict(response.payload),
        iterations=iteration,
        final_message=response,
    )


async def review_loop(
    agent_name: str,
    target: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> ReviewResult:
    """Run an external reviewer iteratively up to *max_iterations* times.

    The loop stops early if the reviewer approves. Otherwise it returns the
    last review produced, with a verdict defaulting to ``needs_discussion``
    when none can be parsed.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    adapter = get(agent_name)
    bridge_id = str(uuid.uuid4())

    message = _create_review_message(
        bridge_id=bridge_id,
        depth=0,
        payload=_build_initial_review_prompt(target),
        metadata={"loop": "review", "max_iterations": max_iterations},
    )

    last_response: AgentMessage | None = None

    for iteration in range(1, max_iterations + 1):
        response = await adapter.run(
            message.payload,
            context=to_adapter_context(message),
        )
        last_response = response
        result = _build_result(response, iteration)

        if result.verdict == ReviewVerdict.APPROVE:
            return result

        if iteration == max_iterations:
            return result

        message = _advance_message(
            response,
            _build_refinement_prompt(target, response.payload, iteration),
            {"loop": "review", "iteration": iteration},
        )

    # Defensive fallback: the loop above always returns before this point.
    if last_response is None:
        raise RuntimeError("review loop did not produce a response")

    return _build_result(last_response, max_iterations)
