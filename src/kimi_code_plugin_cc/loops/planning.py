from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.protocol.messages import AgentMessage

DEFAULT_MAX_ITERATIONS = 3


class PlanResult(BaseModel):
    """Result returned by the planning loop."""

    plan: str
    iterations: int = Field(ge=1)
    final_message: AgentMessage
    status: Literal["complete", "max_iterations"] = "complete"


def _build_initial_prompt(user_prompt: str) -> str:
    return f"Create a concise, step-by-step plan for:\n\n{user_prompt}"


def _build_refinement_prompt(
    user_prompt: str,
    current_plan: str,
    iteration: int,
) -> str:
    return (
        f"Refine the following plan (iteration {iteration}).\n\n"
        f"Current plan:\n{current_plan}\n\n"
        f"Task:\n{user_prompt}"
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
    """Return a copy of *message* with a new payload and metadata.

    Loop iterations are refinement rounds, not recursion, so the depth is kept
    constant (ADR-003).
    """
    return message.model_copy(
        update={"payload": new_payload, "metadata": new_metadata},
    )


async def planning_loop(
    agent_name: str,
    prompt: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> PlanResult:
    """Run a planning agent iteratively up to *max_iterations* times.

    The first iteration asks the agent to create a plan; subsequent iterations
    ask it to refine the previous plan. The loop always returns the final plan
    produced, even if the iteration budget is exhausted.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    adapter = get(agent_name)
    bridge_id = str(uuid.uuid4())

    message = _create_message(
        bridge_id=bridge_id,
        depth=0,
        payload=_build_initial_prompt(prompt),
        metadata={"loop": "planning", "max_iterations": max_iterations},
    )

    current_plan = ""
    last_response: AgentMessage | None = None

    for iteration in range(1, max_iterations + 1):
        response = await adapter.run(
            message.payload,
            context={"message": message.model_dump()},
        )
        last_response = response
        current_plan = response.payload

        if iteration == max_iterations:
            return PlanResult(
                plan=current_plan,
                iterations=iteration,
                final_message=response,
                status="max_iterations",
            )

        message = _advance_message(
            response,
            _build_refinement_prompt(prompt, current_plan, iteration),
            {"loop": "planning", "iteration": iteration},
        )

    # Defensive fallback: the loop above always returns before this point.
    if last_response is None:
        raise RuntimeError("planning loop did not produce a response")

    return PlanResult(
        plan=current_plan,
        iterations=max_iterations,
        final_message=last_response,
        status="max_iterations",
    )
