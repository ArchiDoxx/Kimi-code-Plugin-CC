from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.loops.prompts import plain_prompt
from kimi_code_plugin_cc.protocol.messages import AgentMessage, to_adapter_context
from kimi_code_plugin_cc.transcript import TranscriptRecorder

DEFAULT_MAX_ITERATIONS = 3


class PlanResult(BaseModel):
    """Result returned by the planning loop."""

    plan: str
    iterations: int = Field(ge=1)
    final_message: AgentMessage
    status: Literal["complete", "max_iterations"] = "complete"
    transcript_dir: str | None = None


def _build_initial_prompt(user_prompt: str) -> str:
    return plain_prompt(f"Create a concise, step-by-step plan for:\n\n{user_prompt}")


def _build_refinement_prompt(
    user_prompt: str,
    current_plan: str,
    iteration: int,
) -> str:
    return plain_prompt(
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


def _with_transcript(
    result: PlanResult, recorder: TranscriptRecorder | None
) -> PlanResult:
    """Attach the transcript directory to *result* when recording is on."""
    if recorder is None:
        return result
    return result.model_copy(update={"transcript_dir": recorder.path})


async def planning_loop(
    agent_name: str,
    prompt: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    model: str | None = None,
) -> PlanResult:
    """Run a planning agent iteratively up to *max_iterations* times.

    The first iteration asks the agent to create a plan; subsequent iterations
    ask it to refine the previous plan. The loop always returns the final plan
    produced, even if the iteration budget is exhausted.

    ``model`` (optional) selects a per-call model alias for multi-provider
    setups; it is forwarded to the adapter on every iteration.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    recorder = TranscriptRecorder.start(
        "planning",
        meta={"agent": agent_name, "model": model, "max_iterations": max_iterations},
    )
    final: dict[str, object] = {}
    try:
        result, final = await _run_planning_loop(
            agent_name, prompt, max_iterations, model, recorder
        )
        return result
    except Exception as exc:
        # Crashed runs keep their already-written rounds; record what is known.
        final = {"error": repr(exc)}
        raise
    finally:
        if recorder is not None:
            recorder.finalize(final=final)


async def _run_planning_loop(
    agent_name: str,
    prompt: str,
    max_iterations: int,
    model: str | None,
    recorder: TranscriptRecorder | None,
) -> tuple[PlanResult, dict[str, object]]:
    """Loop body of :func:`planning_loop`; returns the result and final summary."""
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
        started = time.monotonic()
        response = await adapter.run(
            message.payload,
            context=to_adapter_context(message, model=model),
        )
        last_response = response
        previous_plan = current_plan
        current_plan = response.payload
        if recorder is not None:
            recorder.record_round(
                index=iteration,
                role="plan",
                agent=agent_name,
                model=model,
                prompt=message.payload,
                response=response.payload,
                verdict=None,
                duration_s=time.monotonic() - started,
            )

        # Converged: a refinement round returned the same plan, so further
        # iterations add nothing. Stop early and report completion.
        if iteration > 1 and current_plan.strip() == previous_plan.strip():
            result = PlanResult(
                plan=current_plan,
                iterations=iteration,
                final_message=response,
                status="complete",
            )
            final = {"status": "complete", "iterations": iteration}
            return _with_transcript(result, recorder), final

        if iteration == max_iterations:
            result = PlanResult(
                plan=current_plan,
                iterations=iteration,
                final_message=response,
                status="max_iterations",
            )
            final = {"status": "max_iterations", "iterations": iteration}
            return _with_transcript(result, recorder), final

        message = _advance_message(
            response,
            _build_refinement_prompt(prompt, current_plan, iteration),
            {"loop": "planning", "iteration": iteration},
        )

    # Defensive fallback: the loop above always returns before this point.
    if last_response is None:
        raise RuntimeError("planning loop did not produce a response")

    result = PlanResult(
        plan=current_plan,
        iterations=max_iterations,
        final_message=last_response,
        status="max_iterations",
    )
    final = {"status": "max_iterations", "iterations": max_iterations}
    return _with_transcript(result, recorder), final
