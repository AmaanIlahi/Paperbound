from dataclasses import dataclass

from paperbound.pipeline.types import ValidatedField

AUTO_APPROVE_CONFIDENCE = 0.9


@dataclass
class PolicyDecision:
    status: str  # auto | approved | pending
    reason: str


def decide(tool, source_fields: list[ValidatedField], auto_approve_reversible: bool = False) -> PolicyDecision:
    """Approval gate (architecture section 4.7). Deterministic, no LLM."""
    if not tool.side_effect:
        return PolicyDecision("auto", "Read-only tool runs automatically.")
    if getattr(tool, "always_draft", False):
        return PolicyDecision("pending", "Always a draft: approving only saves a draft, nothing is ever sent.")
    if (
        auto_approve_reversible
        and tool.reversible
        and source_fields
        and all(f.confidence >= AUTO_APPROVE_CONFIDENCE for f in source_fields)
    ):
        return PolicyDecision("approved", f"Reversible action and all source fields have confidence >= {AUTO_APPROVE_CONFIDENCE}.")
    if not tool.reversible:
        return PolicyDecision("pending", "Not reversible, so it needs human approval.")
    return PolicyDecision("pending", "Side-effect action: needs human approval (auto-approval is off).")
