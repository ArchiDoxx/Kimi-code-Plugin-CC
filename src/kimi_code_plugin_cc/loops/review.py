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
        f"({verdicts}) and concise comments.\n\n"
        f"On a final line, output exactly `VERDICT: <one of {verdicts}>` "
        "so the verdict is machine-readable.\n\n"
        f"Target:\n{target}"
    )


def _build_refinement_prompt(target: str, previous_review: str, iteration: int) -> str:
    return (
        f"Refine your review (iteration {iteration}).\n\n"
        f"Previous review:\n{previous_review}\n\n"
        f"Target:\n{target}"
    )


# Verdict parsing is deliberately strict and fail-closed.
#
# The reviewer is asked to emit a machine-readable line:
#     VERDICT: approve | request_changes | needs_discussion
# We parse ONLY that line for the verdict. If no structured line is present,
# we fall back to scanning free text — but with negation handling so that
# "I do not approve" / "cannot approve" / "not approved" downgrades to
# needs_discussion BEFORE the approve regex can match.
#
# NOTE on negation asymmetry: the negation guard covers only the APPROVE
# direction (the dangerous one — a false approval). A negated non-approve
# phrase ("I do not request changes") still parses as REQUEST_CHANGES. This
# is intentional and safe: the fail-closed direction is always toward
# non-approval, so an over-eager non-approve verdict cannot produce a false
# green. The structured `VERDICT:` line is the recommended, unambiguous path;
# free-text parsing is the fail-safe fallback.
#
# Non-approve verdicts always take precedence (fail-closed). APPROVE is
# accepted only when no disagreement signal appears anywhere.
_STRUCTURED_VERDICT_RE = re.compile(
    r"^\s*VERDICT\s*:\s*(approve|request_changes|needs_discussion)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Negation patterns: any of these near "approve" downgrades to needs_discussion
# so a negated sentence never reads as approval.
_NEGATED_APPROVE_RE = re.compile(
    r"\b(?:not|do\s+not|don'?t|cannot|can'?t|won'?t|never|no)\s+"
    r"(?:\w+\s+){0,3}?approve",
    re.IGNORECASE,
)
_REQUEST_CHANGES_RE = re.compile(r"\brequest[ _]changes\b")
_NEEDS_DISCUSSION_RE = re.compile(r"\bneeds[ _]discussion\b")
# APPROVE also accepts "approved"/"approves" but NOT the noun "approval" and
# NOT any negated form (handled above).
_APPROVE_RE = re.compile(r"\bapprove[ds]?\b")


def extract_verdict(text: str) -> ReviewVerdict:
    """Parse a verdict from reviewer *text*, fail-closed.

    Preference order:
    1. A structured ``VERDICT: <v>`` line (machine-readable, unambiguous).
    2. Free-text scan with negation handling: any negated "approve" phrase
       forces ``needs_discussion`` before a bare ``approve`` can match.
    3. ``request_changes`` / ``needs_discussion`` take precedence over
       ``approve`` (fail-closed).
    4. No parseable verdict → ``needs_discussion`` (never ``approve``).
    """
    lowered = text.lower()

    # 1. Structured line wins outright.
    structured = _STRUCTURED_VERDICT_RE.search(text)
    if structured:
        return ReviewVerdict(structured.group(1).lower())

    # 2. Fail-closed precedence on free text.
    if _REQUEST_CHANGES_RE.search(lowered):
        return ReviewVerdict.REQUEST_CHANGES
    if _NEEDS_DISCUSSION_RE.search(lowered):
        return ReviewVerdict.NEEDS_DISCUSSION
    # 3. Negation guard: "do not approve" etc. must NOT read as approval.
    if _NEGATED_APPROVE_RE.search(lowered):
        return ReviewVerdict.NEEDS_DISCUSSION
    if _APPROVE_RE.search(lowered):
        return ReviewVerdict.APPROVE
    # 4. Nothing parseable → fail-closed.
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
        verdict=extract_verdict(response.payload),
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
