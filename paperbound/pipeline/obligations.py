from datetime import date, timedelta

from paperbound.pipeline.types import DerivedObligation, ValidatedField
from paperbound.pipeline.validate import BLOCKING_FLAGS


def derive_obligations(fields: list[ValidatedField]) -> tuple[list[DerivedObligation], list[str]]:
    """Deterministic obligations from validated fields. Returns (obligations, skip reasons)."""
    by_name = {f.name: f for f in fields}
    obligations, skipped = [], []

    end, notice = by_name.get("end_date"), by_name.get("notice_period_days")
    problem = unusable_reason(end, "end_date") or unusable_reason(notice, "notice_period_days")
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


def unusable_reason(field: ValidatedField | None, name: str) -> str | None:
    if field is None or field.value is None:
        return f"{name} missing"
    blocked = sorted(set(field.flags) & BLOCKING_FLAGS)
    if "ungrounded" in blocked:
        return f"{name} is ungrounded"
    if blocked:
        return f"{name} failed validation ({', '.join(blocked)})"
    return None


def derive_coi_obligations(fields: list[ValidatedField]) -> tuple[list[DerivedObligation], list[str]]:
    """One 'expiration' obligation per policy whose expiration date (and number) are usable."""
    by_name = {f.name: f for f in fields}
    obligations, skipped = [], []
    indexes = sorted({int(f.name.split(".")[0].split("_")[1]) for f in fields if f.name.startswith("policy_")})
    for i in indexes:
        exp = by_name.get(f"policy_{i}.expiration_date")
        number = by_name.get(f"policy_{i}.number")
        kind = by_name.get(f"policy_{i}.type")
        problem = unusable_reason(exp, f"policy_{i}.expiration_date")
        if problem:
            skipped.append(f"policy {i} expiration skipped: {problem}")
            continue
        due = date.fromisoformat(exp.value)
        sources = [exp.name] + [f.name for f in (number, kind) if unusable_reason(f, "x") is None]
        label = " ".join(str(f.value) for f in (kind, number) if unusable_reason(f, "x") is None) or f"Policy {i}"
        obligations.append(
            DerivedObligation(
                kind="expiration",
                due_date=due,
                description=f"{label} expires {due.isoformat()}: request a renewed certificate of insurance before then",
                source_fields=sources,
            )
        )
    return obligations, skipped
