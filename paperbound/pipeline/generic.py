"""Decide step of the generic fallback (architecture section 4.8). Deterministic: no LLM, no domain math.

A deadline is a usable obligation due date, or a usable date whose label says it is a deadline. "Usable" means present,
free of blocking flags and at or above GENERIC_ACTION_MIN_CONFIDENCE, so a flagged or low-confidence field can never
become an obligation or an action. All this module does with a date is count days to it and sort.
"""

import re
from dataclasses import dataclass
from datetime import date

from paperbound import clock, config
from paperbound.pipeline.impact import Brief, BriefLine
from paperbound.pipeline.types import DerivedObligation, ValidatedField
from paperbound.pipeline.validate import BLOCKING_FLAGS

DEADLINE_LABEL_RE = re.compile(r"\b(?:due|deadline|expir\w*|matur\w*|renew\w*|cut-?off|payable|closing)\b", re.I)
DESCRIPTION_MAX_CHARS = 140


@dataclass
class Deadline:
    due_date: date
    description: str
    source_fields: list[str]  # names, the date field first
    quote: str | None
    page_no: int | None
    is_task: bool = False  # an obligation (a thing to do) rather than a labeled date


def unusable(field: ValidatedField | None) -> str | None:
    """Why a generic field cannot feed an obligation or action, or None if it can."""
    if field is None or field.value is None:
        return "missing"
    blocked = sorted(set(field.flags) & BLOCKING_FLAGS)
    if blocked:
        return "flagged " + ", ".join(blocked)
    if field.confidence < config.generic_action_min_confidence():
        return f"confidence {field.confidence:.2f} is below {config.generic_action_min_confidence():g}"
    return None


def short(text: object) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= DESCRIPTION_MAX_CHARS else text[: DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"


def find_deadlines(fields: list[ValidatedField]) -> tuple[list[Deadline], list[str]]:
    """Usable deadlines sorted by date, and the reasons other candidates were left out."""
    by_name = {f.name: f for f in fields}
    found, skipped = [], []
    for f in fields:
        if not f.name.endswith(".due_date"):
            continue
        desc = by_name.get(f.name.replace(".due_date", ".description"))
        problem = unusable(f) or unusable(desc)
        if problem:
            skipped.append(f"{f.name} skipped: {problem}")
            continue
        found.append(Deadline(date.fromisoformat(f.value), short(desc.value), [f.name, desc.name], f.source_quote, f.page_no, True))
    taken = {d.due_date for d in found}
    for f in fields:
        if not f.name.startswith("date_") or not f.label or not DEADLINE_LABEL_RE.search(f.label):
            continue
        problem = unusable(f)
        if problem:
            skipped.append(f"{f.name} skipped: {problem}")
        elif date.fromisoformat(f.value) not in taken:  # an obligation with the same date already covers it
            found.append(Deadline(date.fromisoformat(f.value), short(f.label).capitalize(), [f.name], f.source_quote, f.page_no))
    return sorted(found, key=lambda d: (d.due_date, d.description)), skipped


def derive_generic_obligations(fields: list[ValidatedField], today: date | None = None) -> tuple[list[DerivedObligation], list[str]]:
    """One deadline obligation per usable deadline, soonest first."""
    deadlines, skipped = find_deadlines(fields)
    return [DerivedObligation("deadline", d.due_date, obligation_text(d), d.source_fields) for d in deadlines], skipped


def obligation_text(d: Deadline) -> str:
    return f"{d.description.rstrip('.')} by {d.due_date.isoformat()}" if d.is_task else f"{d.description}: {d.due_date.isoformat()}"


def days_text(due: date, today: date) -> tuple[int, str]:
    """(signed days until the date, display text): days remaining ahead, "N days ago" once it has passed. Plain date arithmetic."""
    days = (due - today).days
    if days > 0:
        return days, f"{days} day{'s' if days != 1 else ''} (due {due.isoformat()})"
    if days == 0:
        return 0, "due today"
    return days, f"passed {-days} day{'s' if days != -1 else ''} ago"


def build_generic_brief(fields: list[ValidatedField], today: date | None = None) -> Brief:
    """One line per deadline: days remaining for a future date (positive value), "passed N days ago" for a past one
    (negative value), "due today" for today. Only the future ones set brief.due_date."""
    today = clock.today(today)
    deadlines, skipped = find_deadlines(fields)
    brief = Brief(skipped=[{"key": "deadline", "reason": r} for r in skipped])
    for i, d in enumerate(deadlines, start=1):
        days, text = days_text(d.due_date, today)
        note = f"Due {d.due_date.isoformat()}" if days <= 0 else None
        brief.lines.append(BriefLine(f"deadline_{i}", d.description, days, text, d.source_fields, note))
        if days > 0 and brief.due_date is None:
            brief.due_date = d.due_date
    return brief
