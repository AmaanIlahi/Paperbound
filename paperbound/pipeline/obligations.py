from datetime import date, timedelta

from paperbound.pipeline.types import DerivedObligation, ValidatedField
from paperbound.pipeline.validate import BLOCKING_FLAGS


def derive_obligations(fields: list[ValidatedField]) -> tuple[list[DerivedObligation], list[str]]:
    """Deterministic obligations from validated fields. Returns (obligations, skip reasons)."""
    by_name = {f.name: f for f in fields}
    obligations, skipped = [], []

    end, notice = by_name.get("end_date"), by_name.get("notice_period_days")
    problem = _usable(end, "end_date") or _usable(notice, "notice_period_days")
    if problem:
        skipped.append(f"notice deadline skipped: {problem}")
    else:
        end_date = date.fromisoformat(end.value)
        due = end_date - timedelta(days=notice.value)
        obligations.append(
            DerivedObligation(
                kind="deadline",
                due_date=due,
                description=(
                    f"Send written notice to renew or terminate the lease by {due.isoformat()} "
                    f"({notice.value} days before the {end_date.isoformat()} expiration)"
                ),
                source_fields=["end_date", "notice_period_days"],
            )
        )
    return obligations, skipped


def _usable(field: ValidatedField | None, name: str) -> str | None:
    if field is None or field.value is None:
        return f"{name} missing"
    blocked = sorted(set(field.flags) & BLOCKING_FLAGS)
    if "ungrounded" in blocked:
        return f"{name} is ungrounded"
    if blocked:
        return f"{name} failed validation ({', '.join(blocked)})"
    return None
