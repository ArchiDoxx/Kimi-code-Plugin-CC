"""Adversarial dual-review (santa) loop.

The loop converges to ``green`` only when two *independent* reviewers both
approve. v1.0's second reviewer is the host (Claude itself, via a callback the
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
import time
import uuid
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.loops.prompts import review_prompt
from kimi_code_plugin_cc.protocol.messages import AgentMessage, to_adapter_context
from kimi_code_plugin_cc.transcript import TranscriptRecorder

from .review import ReviewResult, ReviewVerdict, extract_verdict

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
    transcript_dir: str | None = None


def _build_initial_review_prompt(target: str) -> str:
    return review_prompt(
        "Perform a thorough code review of the following target and give "
        "concrete comments.\n\n"
        f"Target:\n{target}"
    )


def _build_revision_prompt(
    target: str,
    primary_review: ReviewResult,
    secondary_review: ReviewResult,
    iteration: int,
) -> str:
    """Build the primary reviewer's revision prompt for the next round.

    Both reviews are quoted verbatim, so the verdict contract (restated by
    :func:`review_prompt`) explicitly tells the reviewer not to echo the other
    reviewer's ``VERDICT:`` line — and ``extract_verdict`` fail-closes to
    ``needs_discussion`` if two conflicting lines show up anyway.
    """
    return review_prompt(
        f"Another reviewer disagrees with your review (iteration {iteration}).\n\n"
        f"Your previous review:\n{primary_review.review}\n\n"
        f"Their review:\n{secondary_review.review}\n\n"
        f"Please revise your review of the target:\n{target}"
    )


def _adversarial_prompt(target: str, primary_review: ReviewResult) -> str:
    return review_prompt(
        "You are an INDEPENDENT adversarial reviewer. Another reviewer produced "
        "the review below. Do NOT inherit their conclusion — form your own.\n\n"
        f"Their review:\n{primary_review.review}\n\n"
        "Only approve if you find no real issue.\n\n"
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
    """Return a copy of *message* with a new payload and metadata.

    Loop iterations are refinement rounds, not recursion, so the depth is kept
    constant (ADR-003).
    """
    return message.model_copy(
        update={"payload": new_payload, "metadata": new_metadata},
    )


def _to_review_result(response: AgentMessage, iteration: int) -> ReviewResult:
    return ReviewResult(
        review=response.payload,
        verdict=extract_verdict(response.payload),
        iterations=iteration,
        final_message=response,
    )


async def _secondary_review(
    target: str,
    primary_review: ReviewResult,
    iteration: int,
    adversary_agent: str,
    host_reviewer: HostReviewer | None,
    model: str | None = None,
    recorder: TranscriptRecorder | None = None,
) -> ReviewResult:
    """Obtain the independent second review.

    Prefers the host callback (truly heterogeneous: Claude reviewing itself).
    Falls back to an independent, adversarially-framed external adapter run so
    the loop stays callable when no host is wired (e.g. from the MCP server).
    The host callback may be sync or async; both are awaited correctly.
    """
    if host_reviewer is not None:
        started = time.monotonic()
        result = host_reviewer(target, primary_review)
        if inspect.isawaitable(result):
            result = await result
        if recorder is not None:
            recorder.record_round(
                index=iteration,
                role="adversary",
                agent="host",
                model=None,
                prompt=f"[host callback] target:\n{target}",
                response=result.review,
                verdict=result.verdict.value,
                duration_s=time.monotonic() - started,
            )
        return result
    adapter = get(adversary_agent)
    adversary_context: dict[str, Any] = {"loop": "santa", "role": "adversary"}
    if model is not None:
        adversary_context["model"] = model
    prompt = _adversarial_prompt(target, primary_review)
    started = time.monotonic()
    response = await adapter.run(
        prompt,
        context=adversary_context,
    )
    result = _to_review_result(response, iteration)
    if recorder is not None:
        recorder.record_round(
            index=iteration,
            role="adversary",
            agent=adversary_agent,
            model=model,
            prompt=prompt,
            response=response.payload,
            verdict=result.verdict.value,
            duration_s=time.monotonic() - started,
        )
    return result


def _build_explanation(
    primary: ReviewResult | None,
    secondary: ReviewResult | None,
) -> str:
    if primary is None or secondary is None:
        return "No review produced."
    if primary.verdict != ReviewVerdict.APPROVE:
        return f"Primary reviewer did not approve ({primary.verdict.value})."
    return f"Secondary reviewer did not approve ({secondary.verdict.value})."


def _with_transcript(
    result: SantaResult, recorder: TranscriptRecorder | None
) -> SantaResult:
    """Attach the transcript directory to *result* when recording is on."""
    if recorder is None:
        return result
    return result.model_copy(update={"transcript_dir": recorder.path})


async def santa_loop(
    primary_agent: str,
    target: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    *,
    adversary_agent: str | None = None,
    host_reviewer: HostReviewer | None = None,
    model: str | None = None,
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
        model: Optional per-call model alias for multi-provider setups.
            Forwarded to the primary reviewer and (when no host reviewer is
            wired) to the external adversary as well.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    resolved_adversary = adversary_agent or primary_agent
    recorder = TranscriptRecorder.start(
        "santa",
        meta={
            "agents": {"primary": primary_agent, "adversary": resolved_adversary},
            "model": model,
            "max_iterations": max_iterations,
        },
    )
    final: dict[str, object] = {}
    try:
        result, final = await _run_santa_loop(
            primary_agent,
            target,
            max_iterations,
            resolved_adversary=resolved_adversary,
            host_reviewer=host_reviewer,
            model=model,
            recorder=recorder,
        )
        return result
    except Exception as exc:
        # Crashed runs keep their already-written rounds; record what is known.
        final = {"error": repr(exc)}
        raise
    finally:
        if recorder is not None:
            recorder.finalize(final=final)


async def _run_santa_loop(
    primary_agent: str,
    target: str,
    max_iterations: int,
    *,
    resolved_adversary: str,
    host_reviewer: HostReviewer | None,
    model: str | None,
    recorder: TranscriptRecorder | None,
) -> tuple[SantaResult, dict[str, object]]:
    """Loop body of :func:`santa_loop`; returns the result and final summary."""
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
        started = time.monotonic()
        response = await adapter.run(
            message.payload,
            context=to_adapter_context(message, model=model),
        )
        primary_review = _to_review_result(response, iteration)
        last_primary = primary_review
        if recorder is not None:
            recorder.record_round(
                index=iteration,
                role="primary",
                agent=primary_agent,
                model=model,
                prompt=message.payload,
                response=response.payload,
                verdict=primary_review.verdict.value,
                duration_s=time.monotonic() - started,
            )

        secondary_review = await _secondary_review(
            target,
            primary_review,
            iteration,
            resolved_adversary,
            host_reviewer,
            model=model,
            recorder=recorder,
        )
        last_secondary = secondary_review

        if (
            primary_review.verdict == ReviewVerdict.APPROVE
            and secondary_review.verdict == ReviewVerdict.APPROVE
        ):
            result = SantaResult(
                verdict=SantaVerdict.GREEN,
                primary_review=primary_review,
                secondary_review=secondary_review,
                iterations=iteration,
                explanation="Both reviewers approved.",
            )
            final = {"verdict": SantaVerdict.GREEN.value, "iterations": iteration}
            return _with_transcript(result, recorder), final

        if iteration == max_iterations:
            break

        message = _advance_message(
            response,
            _build_revision_prompt(target, primary_review, secondary_review, iteration),
            {"loop": "santa", "iteration": iteration},
        )

    explanation = _build_explanation(last_primary, last_secondary)
    result = SantaResult(
        verdict=SantaVerdict.RED,
        primary_review=last_primary,
        secondary_review=last_secondary,
        iterations=max_iterations,
        explanation=explanation,
    )
    final = {"verdict": SantaVerdict.RED.value, "iterations": max_iterations}
    return _with_transcript(result, recorder), final
