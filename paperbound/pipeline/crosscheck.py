"""Rent roll <-> T-12 cross-check (architecture section 4.11). Code only: no model, no stored result, nothing new in the database.

Given the stored fields of one completed rent roll run and one completed T-12 run it compares:
  1. property identity: the name and street address read from the first page of each document, fuzzy matched;
     a difference is a `property_mismatch` warning;
  2. rent income: the rent roll's total annual rent against the T-12's base rent revenue (the sum of its usable base_rent
     lines over the twelve months). A difference larger than the tolerance (default 5%) is a `rent_income_mismatch`;
  3. vacancy, as a sanity warning only: the T-12's vacancy and credit loss as a share of its gross income against the rent
     roll's vacant share of area (units when the rent roll has no areas). The T-12 may show less loss than the rent roll's
     vacancy (vacant space earns no rent to lose); it is a `vacancy_sanity` warning only when the T-12 shows more loss than
     the rent roll's vacancy explains by more than ten points.
Each comparison carries both source values, the fields they were computed from, and the difference. A figure that cannot
be computed (no usable lines) is reported as unavailable, never guessed.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from paperbound import clock
from paperbound.pipeline import rent_roll as rr
from paperbound.pipeline import t12 as t12_mod
from paperbound.pipeline.property import addresses_match, names_match
from paperbound.pipeline.types import ValidatedField

DEFAULT_TOLERANCE = 0.05
VACANCY_MARGIN = Decimal("0.10")


def D(x) -> Decimal:
    return Decimal(str(x))


def share(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def pct(x: Decimal) -> str:
    return f"{(x * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def side(run_id: int, label: str, value, text: str, sources: list[str], note: str | None = None) -> dict:
    return {"run_id": run_id, "label": label, "value": value, "text": text, "sources": sources, "note": note}


def comparison(key: str, label: str, status: str, text: str, left: dict | None, right: dict | None, flag: str | None = None, **extra) -> dict:
    return {"key": key, "label": label, "status": status, "flag": flag, "text": text, "rent_roll": left, "t12": right, **extra}


# ---- 1. property identity ----

def compare_property(rr_id: int, t12_id: int, rr_fields: list[ValidatedField], t12_fields: list[ValidatedField]) -> dict:
    a = next((f for f in rr_fields if f.name == "property"), None)
    b = next((f for f in t12_fields if f.name == "property"), None)
    if a is None or b is None:
        who = " and ".join(n for n, f in (("the rent roll", a), ("the T-12", b)) if f is None)
        return comparison("property_identity", "Same property", "unavailable", f"No property name or address could be read from {who}.",
                          side(rr_id, "Rent roll property", None, "not read", [] if a is None else ["property"]),
                          side(t12_id, "T-12 property", None, "not read", [] if b is None else ["property"]))
    va, vb = a.value, b.value
    checks = []
    if va.get("name") and vb.get("name"):
        ok, score = names_match(va["name"], vb["name"])
        checks.append(("name", ok, score))
    if va.get("address") and vb.get("address"):
        ok, score = addresses_match(va["address"], vb["address"])
        checks.append(("address", ok, score))
    show = lambda v: " · ".join(x for x in (v.get("name"), v.get("address")) if x) or "not read"  # noqa: E731
    left, right = side(rr_id, "Rent roll property", va, show(va), ["property"]), side(t12_id, "T-12 property", vb, show(vb), ["property"])
    if not checks:
        return comparison("property_identity", "Same property", "unavailable", "The two documents give no name or address that can be compared.", left, right)
    bad = [name for name, ok, _ in checks if not ok]
    detail = ", ".join(f"{name} similarity {score}" for name, _, score in checks)
    if bad:
        return comparison("property_identity", "Same property", "warning", f"The {' and '.join(bad)} differ, so these may be two different properties ({detail}).",
                          left, right, "property_mismatch", scores={n: s for n, _, s in checks})
    return comparison("property_identity", "Same property", "ok", f"Name and address agree ({detail}).", left, right, scores={n: s for n, _, s in checks})


# ---- 2. rent income ----

def t12_usable_lines(fields: list[ValidatedField]) -> list[ValidatedField]:
    return [f for f in t12_mod.line_fields(fields) if rr.is_usable(f)]


def compare_rent_income(rr_id: int, t12_id: int, rr_fields: list[ValidatedField], t12_fields: list[ValidatedField], tolerance: float, today: date) -> dict:
    brief = {ln.key: ln for ln in rr.build_rent_roll_brief(rr_fields, today).lines}
    total = brief.get("total_annual_rent")
    base_lines = [f for f in t12_usable_lines(t12_fields) if f.value["category"] == "base_rent"]
    flagged_base = [f for f in t12_mod.line_fields(t12_fields) if f.value["category"] == "base_rent" and not rr.is_usable(f)]
    left = side(rr_id, "Rent roll total annual rent", None, "not available", [])
    right = side(t12_id, "T-12 base rent revenue", None, "not available", [])
    if total is not None:
        left = side(rr_id, "Rent roll total annual rent", total.value, total.text.replace(" per year", ""), total.source_fields, total.note)
        totals = next((f for f in rr_fields if f.name == "totals"), None)
        if totals is not None and "totals_mismatch" in totals.flags:
            left["note"] = "; ".join(x for x in (left["note"], "the rent roll's rows do not add up to its stated totals; this is the sum of its usable rows") if x)
    if base_lines:
        amount = sum((sum(t12_mod.zero([D(x) if x is not None else None for x in f.value["months"]]), Decimal(0)) for f in base_lines), Decimal(0))
        right = side(t12_id, "T-12 base rent revenue", float(rr.money(amount)), rr.fmt_money(amount), [f.name for f in base_lines],
                     "sum of the base rent lines over the twelve months")
    problems = []
    if total is None:
        problems.append("the rent roll has no usable total annual rent")
    if not base_lines:
        problems.append("the T-12 has no usable base rent line" + (f" ({len(flagged_base)} flagged)" if flagged_base else ""))
    if problems:
        return comparison("rent_income", "Rent income", "unavailable", "Cannot compare: " + " and ".join(problems) + ".", left, right,
                          tolerance=tolerance)
    base, rent = D(right["value"]), D(left["value"])
    if rent == 0:
        return comparison("rent_income", "Rent income", "unavailable", "The rent roll's total annual rent is zero, so a percentage difference is undefined.", left, right, tolerance=tolerance)
    diff = base - rent
    ratio = diff / rent
    mismatch = abs(ratio) > D(tolerance)
    text = (f"The T-12 shows {rr.fmt_money(base)} of base rent against {rr.fmt_money(rent)} on the rent roll: "
            f"{'+' if diff >= 0 else '-'}{rr.fmt_money(abs(diff))} ({'+' if ratio >= 0 else '-'}{pct(abs(ratio))}), "
            + ("outside" if mismatch else "within") + f" the {pct(D(tolerance))} tolerance.")
    return comparison("rent_income", "Rent income", "mismatch" if mismatch else "ok", text, left, right, "rent_income_mismatch" if mismatch else None,
                      difference=float(rr.money(diff)), difference_share=share(ratio), tolerance=tolerance)


# ---- 3. vacancy sanity ----

def compare_vacancy(rr_id: int, t12_id: int, rr_fields: list[ValidatedField], t12_fields: list[ValidatedField], today: date) -> dict:
    brief = {ln.key: ln for ln in rr.build_rent_roll_brief(rr_fields, today).lines}
    occ = brief.get("occupancy_sqft") or brief.get("occupancy_units")
    lines = t12_usable_lines(t12_fields)
    gross_lines = [f for f in lines if f.value["category"] in ("base_rent", "recoveries", "other_income")]
    loss_lines = [f for f in lines if f.value["category"] == "vacancy_credit_loss"]
    left = side(rr_id, "Rent roll vacancy", None, "not available", [])
    right = side(t12_id, "T-12 vacancy and credit loss", None, "not available", [])
    vacancy = None
    if occ is not None:
        vacancy = 1 - D(occ.value)
        basis = "area" if occ.key == "occupancy_sqft" else "units"
        left = side(rr_id, "Rent roll vacancy", float(vacancy.quantize(Decimal("0.0001"))), f"{pct(vacancy)} of {basis} vacant", occ.source_fields, occ.note)
    loss_share = None
    if gross_lines and loss_lines:
        gross = sum((sum(t12_mod.zero([D(x) if x is not None else None for x in f.value["months"]]), Decimal(0)) for f in gross_lines), Decimal(0))
        loss = sum((abs(sum(t12_mod.zero([D(x) if x is not None else None for x in f.value["months"]]), Decimal(0))) for f in loss_lines), Decimal(0))
        if gross > 0:
            loss_share = loss / gross
            right = side(t12_id, "T-12 vacancy and credit loss", float(loss_share.quantize(Decimal("0.0001"))),
                         f"{rr.fmt_money(loss)} = {pct(loss_share)} of gross income", [f.name for f in loss_lines] + [f.name for f in gross_lines])
    if vacancy is None or loss_share is None:
        return comparison("vacancy_sanity", "Vacancy sanity check", "unavailable", "Not enough usable figures on both sides for a sanity check.", left, right)
    gap = loss_share - vacancy
    if gap > VACANCY_MARGIN:
        return comparison("vacancy_sanity", "Vacancy sanity check", "warning",
                          f"The T-12 loses {pct(loss_share)} of gross income to vacancy and credit loss, {pct(gap)} more than the rent roll's {pct(vacancy)} vacancy explains.",
                          left, right, "vacancy_sanity", difference_share=share(gap))
    return comparison("vacancy_sanity", "Vacancy sanity check", "ok",
                      f"The T-12's loss ({pct(loss_share)} of gross income) is not out of line with the rent roll's {pct(vacancy)} vacancy.",
                      left, right, difference_share=share(gap))


def cross_check(rr_id: int, t12_id: int, rr_fields: list[ValidatedField], t12_fields: list[ValidatedField],
                tolerance: float = DEFAULT_TOLERANCE, today: date | None = None) -> dict:
    """The report: each comparison with both sides, their sources (field names, per run), the difference and a status."""
    today = clock.today(today)
    comparisons = [
        compare_property(rr_id, t12_id, rr_fields, t12_fields),
        compare_rent_income(rr_id, t12_id, rr_fields, t12_fields, tolerance, today),
        compare_vacancy(rr_id, t12_id, rr_fields, t12_fields, today),
    ]
    flags = [{"flag": c["flag"], "severity": "mismatch" if c["status"] == "mismatch" else "warning", "key": c["key"]} for c in comparisons if c["flag"]]
    unavailable = [c["key"] for c in comparisons if c["status"] == "unavailable"]
    return {
        "rent_roll_run_id": rr_id, "t12_run_id": t12_id, "as_of": today.isoformat(), "tolerance": tolerance,
        "status": "review" if flags else ("incomplete" if unavailable else "ok"), "flags": flags, "unavailable": unavailable,
        "comparisons": comparisons,
    }
