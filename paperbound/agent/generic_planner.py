"""Act step of the generic fallback (architecture section 4.8): proposals built entirely by code, no LLM.

A document of an unsupported type has no trusted recipient and no profile, so the only proposals are a calendar
reminder for each usable future deadline and one "flag for human review". Titles and descriptions are templates filled
from grounded fields; nothing the model wrote reaches a proposal except the short labels shown beside each field.
"""

from datetime import date

from paperbound import clock
from paperbound.agent import policy
from paperbound.agent.planner import PlannedAction, PlanResult
from paperbound.agent.profiles import RUN_ID_PLACEHOLDER
from paperbound.agent.tools import TOOLS
from paperbound.pipeline.generic import find_deadlines, unusable
from paperbound.pipeline.types import DerivedObligation, ValidatedField


def plan_generic_actions(
    fields: list[ValidatedField],
    obligations: list[DerivedObligation] | None = None,
    existing_obligations: list[dict] | None = None,
    today: date | None = None,
    brief=None,
    doc_type: str = "generic",
    type_label: str | None = None,
) -> PlanResult:
    today = clock.today(today)
    by_name = {f.name: f for f in fields}
    result = PlanResult(model=None)
    label = type_label or "unknown"

    calendar = TOOLS["create_calendar_event"]
    deadlines, _ = find_deadlines(fields)
    for d in deadlines:
        if d.due_date <= today:
            continue
        sources = [by_name[n] for n in d.source_fields]
        if any(unusable(f) for f in sources):  # find_deadlines already checked; kept so this module is safe on its own
            continue
        lines = [d.description]
        if d.quote:
            lines.append(f'Evidence (page {d.page_no}): "{d.quote}"')
        lines += [
            f"Source: /#run={RUN_ID_PLACEHOLDER}&page={d.page_no}",
            f"Generic extraction (unsupported type: {label}): confidence is capped, check the page before relying on it.",
        ]
        args = {"title": f"Due: {d.description}", "date": d.due_date.isoformat(), "description": "\n".join(lines)}
        problem = calendar.validate_args(args)
        if problem:
            result.rejected.append({"tool": calendar.name, "reason": problem})
            continue
        decision = policy.decide(calendar, sources)
        result.actions.append(PlannedAction(
            tool=calendar.name, args=args, source_field_ids=[], source_fields=d.source_fields,
            rationale=f"A deadline on {d.due_date.isoformat()} was found and grounded on page {d.page_no}.",
            policy_status=decision.status, policy_reason=decision.reason, group_key="deadline_reminders", group_mode="bundle",
        ))

    flag = TOOLS["flag_for_review"]
    left_out = [f"{f.name} ({unusable(f)})" for f in fields if f.value is not None and unusable(f)]
    reason = (
        f"Generic extraction of an unsupported document type ({label}). Confidence is capped, so check each field against "
        "the page before relying on it."
        + (f" Left out of every proposal: {'; '.join(left_out)}." if left_out else "")
    )
    decision = policy.decide(flag, [])
    result.actions.append(PlannedAction(
        tool=flag.name, args={"reason": reason}, source_field_ids=[], source_fields=[],
        rationale="Paperbound has no schema for this document type, so a person should confirm what was read.",
        policy_status=decision.status, policy_reason=decision.reason,
    ))
    if left_out:
        result.notes.append(f"{len(left_out)} field(s) were flagged or low-confidence and produced no action.")
    return result
