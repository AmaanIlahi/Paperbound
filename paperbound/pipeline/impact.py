"""Decision brief: deterministic numbers about what the deadline means. No LLM, grounded fields only.

Every line cites the fields it was computed from (by name; the API maps names to field ids). A line whose inputs
are missing or blocked is skipped with a reason, never guessed.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from paperbound.pipeline.obligations import derive_obligations, unusable_reason
from paperbound.pipeline.types import ValidatedField

ESCALATION_CAVEAT = "Illustrative: the lease sets the renewal rent separately."
HOLDOVER_CAVEAT = "Assumes the current rent is still in effect at expiration."


@dataclass
class BriefLine:
    key: str
    label: str
    value: object  # ISO date string, int, or float (money is rounded to cents)
    text: str  # display text
    source_fields: list[str]
    note: str | None = None


@dataclass
class Brief:
    lines: list[BriefLine] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {"key", "reason"}
    due_date: date | None = None

    def line(self, key: str) -> BriefLine | None:
        return next((ln for ln in self.lines if ln.key == key), None)

    def value(self, key: str):
        ln = self.line(key)
        return ln.value if ln else None


def money(amount: float | Decimal) -> Decimal:
    return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_money(amount: float | Decimal) -> str:
    return f"${money(amount):,.2f}"


def fmt_date(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def fmt_percent(value: float) -> str:
    return f"{value:g}%"


def build_brief(fields: list[ValidatedField], today: date | None = None) -> Brief:
    """Compute the decision brief from validated fields (blocked fields are never used)."""
    today = today or date.today()
    by_name = {f.name: f for f in fields}
    brief = Brief()

    def need(key: str, *names: str) -> list[ValidatedField] | None:
        """The named fields if all are usable; otherwise record why the line is skipped."""
        problems = [p for n in names if (p := unusable_reason(by_name.get(n), n))]
        if problems:
            brief.skipped.append({"key": key, "reason": "; ".join(problems)})
            return None
        return [by_name[n] for n in names]

    # deadline and days remaining (reuses the obligation rule, so the two can never disagree)
    obligations, skipped = derive_obligations(fields)
    if obligations:
        due = obligations[0].due_date
        brief.due_date = due
        days = (due - today).days
        brief.lines.append(BriefLine("deadline", "Notice deadline", due.isoformat(), fmt_date(due), ["end_date", "notice_period_days"]))
        text = f"{days} days left" if days >= 0 else f"{-days} days overdue"
        brief.lines.append(BriefLine("days_remaining", "Days remaining", days, text, ["end_date", "notice_period_days"]))
    else:
        for key in ("deadline", "days_remaining"):
            brief.skipped.append({"key": key, "reason": "; ".join(skipped) or "no deadline derived"})

    # rent today, and one escalation step later
    current = None
    if need("current_rent", "current_rent"):
        current = money(by_name["current_rent"].value)
        brief.lines.append(BriefLine("current_rent", "Current monthly rent", float(current), fmt_money(current) + " / month", ["current_rent"]))
    if current is not None and need("next_rent", "current_rent", "rent_escalation_percent"):
        pct = by_name["rent_escalation_percent"].value
        nxt = money(current * (1 + Decimal(str(pct)) / 100))
        brief.lines.append(
            BriefLine("next_rent", f"Rent after one more {fmt_percent(pct)} step", float(nxt), fmt_money(nxt) + " / month",
                      ["current_rent", "rent_escalation_percent"], ESCALATION_CAVEAT)
        )
    elif current is None:
        brief.skipped.append({"key": "next_rent", "reason": "current_rent unusable"})

    # what missing the deadline costs
    if current is not None and need("holdover_rent", "current_rent", "holdover_rate_percent"):
        pct = by_name["holdover_rate_percent"].value
        holdover = money(current * Decimal(str(pct)) / 100)
        brief.lines.append(
            BriefLine("holdover_rent", f"Holdover rent ({fmt_percent(pct)})", float(holdover), fmt_money(holdover) + " / month",
                      ["current_rent", "holdover_rate_percent"], HOLDOVER_CAVEAT)
        )
        extra = holdover - current
        brief.lines.append(
            BriefLine("holdover_extra", "Extra cost per month if missed", float(extra), fmt_money(extra) + " / month",
                      ["current_rent", "holdover_rate_percent"], HOLDOVER_CAVEAT)
        )
        brief.lines.append(BriefLine("holdover_percent", "Holdover rate", float(pct), fmt_percent(pct), ["holdover_rate_percent"]))
    elif current is None:
        for key in ("holdover_rent", "holdover_extra", "holdover_percent"):
            brief.skipped.append({"key": key, "reason": "current_rent unusable"})

    # how the notice must be given
    if need("notice_method", "notice_method"):
        method = str(by_name["notice_method"].value)
        brief.lines.append(BriefLine("notice_method", "Notice must be sent by", method, method, ["notice_method"]))
    if need("notice_section", "notice_section"):
        section = str(by_name["notice_section"].value)
        brief.lines.append(BriefLine("notice_section", "Notice clause", section, section, ["notice_section"]))
    if need("security_deposit", "security_deposit"):
        deposit = money(by_name["security_deposit"].value)
        brief.lines.append(BriefLine("security_deposit", "Security deposit", float(deposit), fmt_money(deposit), ["security_deposit"]))
    return brief
