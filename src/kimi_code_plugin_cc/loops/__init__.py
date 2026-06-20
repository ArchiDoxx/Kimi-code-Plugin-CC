"""Loop skills: planning, review, and adversarial (santa) review."""

from kimi_code_plugin_cc.loops.planning import PlanResult, planning_loop
from kimi_code_plugin_cc.loops.review import ReviewResult, ReviewVerdict, review_loop
from kimi_code_plugin_cc.loops.santa import SantaResult, SantaVerdict, santa_loop

__all__ = [
    "PlanResult",
    "ReviewResult",
    "ReviewVerdict",
    "SantaResult",
    "SantaVerdict",
    "planning_loop",
    "review_loop",
    "santa_loop",
]

