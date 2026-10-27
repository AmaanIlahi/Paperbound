"""Act step of the tabular document types (rent roll, T-12): proposals built entirely by code, no LLM.

Rent roll: one all-day reminder on the end date of each usable lease expiring within 24 months (the cap is
MAX_REMINDERS), grouped by whether it ends within 12 months or within 24, and a review flag when any check failed or
nothing could be read. A row with a blocking flag never feeds a reminder. T-12: a review flag when any check failed or a
month looks off. There are no email drafts, no webhooks and no tracker record: a table gives Paperbound no trusted
recipient. Titles and descriptions are templates filled from parsed cells.
"""

from datetime import date

from paperbound import clock
from paperbound.agent import policy
from paperbound.agent.planner import PlannedAction, PlanResult
from paperbound.agent.profiles import RUN_ID_PLACEHOLDER
from paperbound.agent.tools import TOOLS
from paperbound.pipeline.rent_roll import NEAR_MONTHS, add_months, fmt_money, fmt_whole, is_usable, table_flags
from paperbound.pipeline.types import DerivedObligation, ValidatedField

MAX_REMINDERS = 25
FLAG_LABELS = {
    "totals_mismatch": "the rows do not add up to the stated totals",
    "row_math_mismatch": "a row's rent, area and rate do not agree",
    "bad_dates": "a lease starts on or after the day it ends",
    "header_mapping_suspect": "a column mapped to area or rent is not mostly numbers",
    "cell_unparsed": "a cell could not be read as a number or date",
    "vacant_row_has_data": "a vacant row has a tenant, rent or dates",
    "status_conflict": "the status and the tenant disagree",
    "invalid_amount": "a negative figure",
    "line_sum_mismatch": "a line's months do not add up to its total",
    "subtotal_mismatch": "a stated subtotal differs from the lines above it",
    "noi_mismatch": "the stated NOI differs from revenue minus operating expenses",
    "category_suspect": "a line that reads like a below-the-line item was counted as an operating expense",
    "month_anomaly": "a month is more than 25% away from the line's median",
    "not_found": "no table was found",
}


def review_reason(kind_label: str, fields: list[ValidatedField]) -> str | None:
    found = table_flags(fields)
    if not found:
        return None
    parts = []
    for flag, names in found.items():
        shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
        parts.append(f"{FLAG_LABELS.get(flag, flag)} ({flag}: {shown})")
    return f"{kind_label} needs a person to check it before anyone relies on it: " + "; ".join(parts) + "."


def _review_action(reason: str, fields: list[ValidatedField], rationale: str) -> PlannedAction:
    tool = TOOLS["flag_for_review"]
    decision = policy.decide(tool, [])
    return PlannedAction(tool=tool.name, args={"reason": reason}, source_field_ids=[], source_fields=[], rationale=rationale,
                         policy_status=decision.status, policy_reason=decision.reason)


def plan_rent_roll_actions(
    fields: list[ValidatedField],
    obligations: list[DerivedObligation] | None = None,
    existing_obligations: list[dict] | None = None,
    today: date | None = None,
    brief=None,
    doc_type: str = "rent_roll",
    type_label: str | None = None,
) -> PlanResult:
    today = clock.today(today)
    by_name = {f.name: f for f in fields}
    result = PlanResult(model=None)
    calendar = TOOLS["create_calendar_event"]
    near_limit = add_months(today, NEAR_MONTHS)
    proposed = 0
    for o in obligations or []:
        row = by_name.get(o.source_fields[0])
        if row is None or not is_usable(row) or not isinstance(row.value, dict):
            continue  # a flagged row never feeds an action
        if proposed >= MAX_REMINDERS:
            result.notes.append(f"Only the {MAX_REMINDERS} soonest expirations got a reminder; {len(obligations) - proposed} later one(s) did not.")
            break
        v = row.value
        within_12 = o.due_date <= near_limit
        tier = "within 12 months" if within_12 else "within 24 months"
        lines = [
            f"{v.get('tenant') or 'Tenant not stated'} · {v.get('unit') or 'unit not stated'}: lease ends {o.due_date.isoformat()} (expires {tier}).",
            "Facts from the rent roll: " + ", ".join(p for p in (
                f"{v['sqft']:,.0f} sf" if v.get("sqft") is not None else None,
                f"{fmt_money(v['annual_rent_used'])} per year ({v['annual_source']})" if v.get("annual_rent_used") is not None else None,
            ) if p) + ".",
            f'Evidence (page {row.page_no}): "{" | ".join(v["cells"])[:200]}"',
            f"Source: /#run={RUN_ID_PLACEHOLDER}&page={row.page_no}",
        ]
        args = {"title": f"Lease expires: {v.get('tenant') or v.get('unit') or row.name}", "date": o.due_date.isoformat(), "description": "\n".join(lines)}
        problem = calendar.validate_args(args)
        if problem:
            result.rejected.append({"tool": calendar.name, "reason": problem})
            continue
        decision = policy.decide(calendar, [row])
        result.actions.append(PlannedAction(
            tool=calendar.name, args=args, source_field_ids=[], source_fields=[row.name],
            rationale=f"The lease ends {o.due_date.isoformat()}, {tier}; the end date was read from page {row.page_no} and passed every row check.",
            policy_status=decision.status, policy_reason=decision.reason,
            group_key="lease_expirations_12m" if within_12 else "lease_expirations_24m", group_mode="bundle",
        ))
        proposed += 1
    reason = review_reason("This rent roll", fields)
    if reason:
        result.actions.append(_review_action(reason, fields, "Paperbound found a problem it cannot resolve by itself, so a person should confirm what was read."))
        result.notes.append("A review flag was raised; flagged rows produced no reminder.")
    return result


def plan_t12_actions(
    fields: list[ValidatedField],
    obligations: list[DerivedObligation] | None = None,
    existing_obligations: list[dict] | None = None,
    today: date | None = None,
    brief=None,
    doc_type: str = "t12",
    type_label: str | None = None,
) -> PlanResult:
    clock.today(today)
    result = PlanResult(model=None)
    reason = review_reason("This operating statement", fields)
    if reason:
        result.actions.append(_review_action(reason, fields, "Paperbound found a problem it cannot resolve by itself, so a person should confirm what was read."))
    return result
