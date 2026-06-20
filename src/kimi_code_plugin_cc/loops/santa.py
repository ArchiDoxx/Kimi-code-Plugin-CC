from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.protocol.messages import AgentMessage, increment_depth

from .review import ReviewResult, ReviewVerdict, _extract_verdict

DEFAULT_MAX_ITERATIONS = 3


class SantaVerdict(StrEnum):
    """Possible outcomes of the adversarial dual-review (santa) loop."""

    GREEN = "green"
    RED = "red"


class SantaResult(BaseModel):
    """Result returned by the santa loop."""

    verdict: SantaVerdict
    primary_review: ReviewResult
    host_review: ReviewResult
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
    host_review: ReviewResult,
    iteration: int,
) -> str:
    return (
        f"The host reviewer disagrees with your review (iteration {iteration}).\n\n"
        f"Your previous review:\n{primary_review.review}\n\n"
        f"Host review:\n{host_review.review}\n\n"
        f"Please revise your review of the target:\n{target}"
    )


def _create_message(
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


def _request_host_review(target: str, primary_review: ReviewResult) -> ReviewResult:
    """v0.5 placeholder for the host (Claude itself) reviewer.

    In the skill layer this will be replaced by a callback to the host. The
    default implementation echoes the primary review so that tests can inject a
    deterministic host response by monkey-patching this function.
    """
    verdict_text = primary_review.verdict.value
    return ReviewResult(
        review=f"[HOST] Reviewed target. Primary verdict: {verdict_text}.",
        verdict=primary_review.verdict,
        iterations=1,
        final_message=AgentMessage(
            bridge_id=primary_review.final_message.bridge_id,
            depth=0,
            approval_policy="read-only",
            payload="host review placeholder",
            metadata={"loop": "santa", "role": "host"},
        ),
    )


def _build_explanation(
    primary: ReviewResult | None,
    host: ReviewResult | None,
) -> str:
    if primary is None or host is None:
        return "No review produced."
    if primary.verdict != ReviewVerdict.APPROVE:
        return f"Primary reviewer did not approve ({primary.verdict.value})."
    return f"Host reviewer did not approve ({host.verdict.value})."


def santa_loop(
    primary_agent: str,
    target: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> SantaResult:
    """Run an adversarial dual-review loop.

    The primary agent reviews the target; the host (Claude itself) reviews the
    same target independently. The loop only returns ``green`` when both
    reviewers approve. On disagreement the primary agent gets up to
    *max_iterations* chances to revise, after which the result fail-closes to
    ``red``.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    adapter = get(primary_agent)
    bridge_id = str(uuid.uuid4())

    message = _create_message(
        bridge_id=bridge_id,
        depth=0,
        payload=_build_initial_review_prompt(target),
        metadata={"loop": "santa", "max_iterations": max_iterations},
    )

    last_primary: ReviewResult | None = None
    last_host: ReviewResult | None = None

    for iteration in range(1, max_iterations + 1):
        response = adapter.run(
            message.payload,
            context={"message": message.model_dump()},
        )
        primary_review = _to_review_result(response, iteration)
        last_primary = primary_review

        host_review = _request_host_review(target, primary_review)
        last_host = host_review

        if (
            primary_review.verdict == ReviewVerdict.APPROVE
            and host_review.verdict == ReviewVerdict.APPROVE
        ):
            return SantaResult(
                verdict=SantaVerdict.GREEN,
                primary_review=primary_review,
                host_review=host_review,
                iterations=iteration,
                explanation="Both reviewers approved.",
            )

        if iteration == max_iterations:
            break

        message = _advance_message(
            response,
            _build_revision_prompt(target, primary_review, host_review, iteration),
            {"loop": "santa", "iteration": iteration},
        )

    explanation = _build_explanation(last_primary, last_host)
    return SantaResult(
        verdict=SantaVerdict.RED,
        primary_review=last_primary,
        host_review=last_host,
        iterations=max_iterations,
        explanation=explanation,
    )
