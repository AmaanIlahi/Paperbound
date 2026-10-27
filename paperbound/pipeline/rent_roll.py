"""Rent roll (architecture section 4.9): Extract (tables by code, one mapping call), Verify, Decide, Act inputs.

The model's only job is `mapping.map_rent_roll_columns`: which column holds which field. Everything else is code: the
table is found by `tables.extract_tables`, each cell is parsed by the shared amount and date parsers, and every check,
total and metric is arithmetic on parsed values. A row keeps its page and bounding box. Nothing is guessed: if no table
is found, or no table maps to a rent roll, the run says so and asks for a review.

Fields stored for a run: `mapping` (the header and the column mapping), `row_N` (one per unit row; its value is a dict
of parsed cells), `totals` (the stated totals row, kept apart from the rows).
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from paperbound import clock
from paperbound.pipeline import tables as tables_mod
from paperbound.pipeline.extract import ExtractionResult
from paperbound.pipeline.impact import Brief, BriefLine
from paperbound.pipeline.mapping import MappingResult, map_rent_roll_columns
from paperbound.pipeline.property import property_field
from paperbound.pipeline.types import DerivedObligation, ValidatedField
from paperbound.pipeline.validate import BLOCKING_FLAGS, CHECK_FLAGS, _dates_in, amounts_in, field_confidence, squash
from paperbound.schemas.cre import NUMERIC_RENT_ROLL_FIELDS, RENT_ROLL_FIELDS

DAYS_PER_YEAR = Decimal("365.25")
NUMERIC_SHARE_MIN = 0.7  # a column mapped to sqft or rent must be at least this share numeric
PSF_TOLERANCE = Decimal("0.015")  # sqft x $/sf against annual rent: rate rounding
MONTHLY_TOLERANCE = Decimal("0.005")  # monthly x 12 against annual rent
EXPIRY_HORIZON_MONTHS = 24
NEAR_MONTHS = 12
MAX_TABLES_MAPPED = 2
MIN_BODY_ROWS = 2

VACANT_RE = re.compile(r"\b(?:vacant|vacancy|available|unleased|for\s+lease)\b", re.I)
OCCUPIED_RE = re.compile(r"^\W*(?:occupied|leased|current)\W*$", re.I)
TOTAL_RE = re.compile(r"^\W*(?:(?:grand|property|building|center|portfolio)\s+)?totals?\W*$", re.I)
TOTAL_START_RE = re.compile(r"^\W*(?:(?:grand|property|building|center|portfolio)\s+)?totals?\b", re.I)
SUBTOTAL_RE = re.compile(r"sub\W*totals?|floor\s+totals?|totals?\s+for\b|totals?\s+(?:occupied|vacant|leased)", re.I)
MTM_RE = re.compile(r"^\W*(?:mtm|m\W*t\W*m|month[\s-]*to[\s-]*month|holding\s+over|holdover)\W*$", re.I)
BLANK_RE = re.compile(r"^\W*(?:-+|—|–|n/?a|none|tbd|nil)?\W*$", re.I)
MONTHLY_PSF_RE = re.compile(r"\b(?:mo|mth|month|monthly)\b", re.I)
_DECORATION = re.compile(r"\b(?:usd|eur|gbp|sf|rsf|psf|sq\.?\s*ft\.?|per|yr|mo|year|month|annual|monthly)\b|[$€£/*]", re.I)
_PLAIN_NUMBER = re.compile(r"\s*\(?\s*-?\s*\d[\d,]*(?:\.\d+)?\s*\)?\s*")
_SHORT_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b")


def D(value) -> Decimal:
    return Decimal(str(value))


def money(value) -> Decimal:
    return D(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_money(value) -> str:
    return f"${money(value):,.2f}"


def fmt_whole(value) -> str:
    return f"${D(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP):,.0f}"


def fmt_sqft(value) -> str:
    return f"{D(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP):,.0f} sf"


# ---------------------------------------------------------------------------------------------------------------
# cell parsers (the shared amount and date parsers, applied to one cell)
# ---------------------------------------------------------------------------------------------------------------

UNPARSED = object()


def parse_number(text: str):
    """(Decimal) for a cell holding exactly one number, None for a blank cell, UNPARSED for anything else."""
    text = squash(text).strip()
    if BLANK_RE.match(text):
        return None
    if not _PLAIN_NUMBER.fullmatch(_DECORATION.sub(" ", text)):
        return UNPARSED  # the cell holds more than a number ("TBD-ish 2k", "1,200 / 1,400"): do not pick digits out of words
    found = sorted(set(amounts_in(text)))
    return D(found[0]) if len(found) == 1 else UNPARSED


def parse_date(text: str):
    """(date), 'mtm' for month-to-month wording, None for a blank cell, UNPARSED for anything else. A numeric date with a
    two-digit year (11/28/19) is read as 20YY."""
    text = squash(text).strip()
    if BLANK_RE.match(text):
        return None
    if MTM_RE.match(text):
        return "mtm"
    found = sorted(_dates_in(text))
    if not found:
        short = _SHORT_DATE.findall(text)
        if len(short) == 1:
            m, d, y = (int(v) for v in short[0])
            try:
                found = [date(2000 + y, m, d).isoformat()]
            except ValueError:
                found = []
    return date.fromisoformat(found[0]) if len(found) == 1 else UNPARSED


def is_vacant_text(text: str) -> bool:
    return bool(VACANT_RE.search(text or ""))


# ---------------------------------------------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class RRRow:
    index: int  # position among the unit rows, 1-based (the field is named row_<index>)
    page_no: int
    bbox: list[float]
    cells: list[str]
    values: dict = field(default_factory=dict)  # parsed
    unparsed: list[str] = field(default_factory=list)  # fields whose cell could not be read
    flags: list[str] = field(default_factory=list)
    label: str = ""


@dataclass
class RentRollExtraction:
    found: bool
    reason: str = ""
    header: list[str] = field(default_factory=list)
    header_page: int | None = None
    header_bbox: list[float] | None = None
    mapping: dict[int, str] = field(default_factory=dict)  # column -> field (only mapped columns)
    mapping_notes: list[str] = field(default_factory=list)
    psf_basis: str = "annual"
    rows: list[RRRow] = field(default_factory=list)
    totals: RRRow | None = None
    subtotals: list[RRRow] = field(default_factory=list)
    suspect_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)

    def count(self) -> int:
        return len(self.rows)

    def summary(self) -> str:
        if not self.found:
            return f"no rent roll table ({self.reason})"
        return (f"{len(self.rows)} unit row(s), {len(self.mapping)} of {len(self.header)} columns mapped, "
                + ("totals row found" if self.totals else "no totals row"))

    def explain(self) -> str:
        if not self.found:
            return f"Code looked for a table and found none that reads as a rent roll: {self.reason}. Nothing is guessed; the run asks for a review."
        mapped = ", ".join(f"{self.header[c]} → {f}" for c, f in sorted(self.mapping.items()))
        return (f"Code found the table with PyMuPDF (page(s) {', '.join(map(str, self.pages))}) and read every cell with the shared "
                f"parsers. The model only mapped the columns: {mapped}.")


def clean_mapping(raw: dict[int, str], ncols: int) -> tuple[dict[int, str], list[str]]:
    """Keep known fields on existing columns, each field once (the first column wins)."""
    mapping, notes, used = {}, [], set()
    for col in sorted(raw):
        name = raw[col]
        if name == "ignore" or name not in RENT_ROLL_FIELDS:
            continue
        if not 0 <= col < ncols:
            notes.append(f"column {col} does not exist and was dropped")
        elif name in used:
            notes.append(f"{name} was mapped to a second column ({col}); the first was kept")
        else:
            mapping[col] = name
            used.add(name)
    return mapping, notes


def usable_mapping(mapping: dict[int, str]) -> bool:
    fields = set(mapping.values())
    return bool(fields & {"unit", "tenant"}) and bool(fields & set(NUMERIC_RENT_ROLL_FIELDS))


def row_kind(row: "RRRow", mapping: dict[int, str]) -> str:
    """'unit', 'total' or 'subtotal'. A row whose label starts with "Total" is a total only if the label is just that
    ("Total", "Grand Total") or the row carries no lease dates, so a tenant called "Total Wine" stays a unit."""
    labels = [row.cells[c] for c, f in sorted(mapping.items()) if f in ("unit", "tenant") and c < len(row.cells)]
    labels.append(next((c for c in row.cells if c.strip()), ""))
    if any(SUBTOTAL_RE.search(text) for text in labels):
        return "subtotal"
    has_dates = bool(row.values.get("lease_start") or row.values.get("lease_end") or row.values.get("mtm"))
    for text in labels:
        if TOTAL_RE.match(text) or (TOTAL_START_RE.match(text) and not has_dates):
            return "total"
    return "unit"


def parse_row(row: RRRow, mapping: dict[int, str], psf_basis: str) -> None:
    raw = {f: row.cells[c] if c < len(row.cells) else "" for c, f in mapping.items()}
    values: dict = {f: None for f in RENT_ROLL_FIELDS}
    values["unit"] = squash(raw.get("unit", "")).strip() or None
    values["tenant"] = squash(raw.get("tenant", "")).strip() or None
    values["status_text"] = squash(raw.get("status", "")).strip() or None
    for name in ("sqft", "monthly_rent", "annual_rent", "rent_psf"):
        if name in raw:
            parsed = parse_number(raw[name])
            if parsed is UNPARSED:
                row.unparsed.append(name)
            else:
                values[name] = parsed
    values["mtm"] = False
    for name in ("lease_start", "lease_end"):
        if name in raw:
            parsed = parse_date(raw[name])
            if parsed is UNPARSED:
                row.unparsed.append(name)
            elif parsed == "mtm":
                values["mtm"] = True
            else:
                values[name] = parsed
    if values["rent_psf"] is not None and psf_basis == "monthly":
        values["rent_psf"] = values["rent_psf"] * 12  # a monthly rate, annualised by code
    row.values = values
    row.label = " · ".join(v for v in (values["unit"], values["tenant"]) if v)[:60]


def numeric_share(rows: list[RRRow], mapping: dict[int, str], name: str) -> tuple[int, int]:
    """(parseable, non-blank) cells of a mapped numeric column over the unit rows. Blank cells and vacancy words are not counted."""
    column = next((c for c, f in mapping.items() if f == name), None)
    ok = total = 0
    for row in rows:
        text = row.cells[column] if column is not None and column < len(row.cells) else ""
        if BLANK_RE.match(squash(text).strip()) or is_vacant_text(text):
            continue
        total += 1
        ok += parse_number(text) is not UNPARSED
    return ok, total


def build_table(table: tables_mod.Table, mapping: dict[int, str], notes: list[str]) -> RentRollExtraction:
    psf_col = next((c for c, f in mapping.items() if f == "rent_psf"), None)
    basis = "monthly" if psf_col is not None and MONTHLY_PSF_RE.search(table.header[psf_col]) else "annual"
    out = RentRollExtraction(
        True, header=table.header, header_page=table.header_page, header_bbox=table.header_bbox, mapping=mapping,
        mapping_notes=notes, psf_basis=basis, notes=list(table.notes), pages=list(table.pages),
    )
    index = 0
    totals: list[RRRow] = []
    for tr in table.rows:
        row = RRRow(0, tr.page_no, tr.bbox, tr.cells)
        parse_row(row, mapping, basis)
        kind = row_kind(row, mapping)
        if kind == "unit":
            index += 1
            row.index = index
            out.rows.append(row)
        else:
            (totals if kind == "total" else out.subtotals).append(row)
    if totals:  # several rows start with "Total": the plainly named one, else the last, is the table's total
        exact = [r for r in totals if TOTAL_RE.match(next((c for c in r.cells if c.strip()), ""))]
        out.totals = (exact or totals)[-1]
        out.subtotals += [r for r in totals if r is not out.totals]
    for name in NUMERIC_RENT_ROLL_FIELDS:
        if name not in mapping.values():
            continue
        ok, total = numeric_share(out.rows, mapping, name)
        if total and ok / total < NUMERIC_SHARE_MIN:  # an empty column has nothing to misread; its metrics are simply absent
            out.suspect_columns.append(name)
    return out


def extract_rent_roll(
    pages, pdf: bytes | None, today=None, mapper: Callable | None = None, client=None,
) -> ExtractionResult:
    """Find the rent roll table with PyMuPDF, ask the model for the column mapping, parse every cell in code.

    `mapper(header, body_rows)` returns a MappingResult (tests inject one). Without the PDF bytes (the stored file is
    gone) there is nothing to detect tables in, which is reported, not guessed."""
    map_calls = MappingResult({}, None, 0, 0.0)
    if not pdf:
        return ExtractionResult(RentRollExtraction(False, "the PDF file is not available, so its tables cannot be detected"), "", 0, 0.0)
    detected = [t for t in tables_mod.extract_tables(pdf) if t.ncols >= 4 and len(t.rows) >= MIN_BODY_ROWS]
    if not detected:
        return ExtractionResult(RentRollExtraction(False, "no table with four or more columns was found in the PDF"), "", 0, 0.0)
    detected.sort(key=lambda t: len(t.rows), reverse=True)
    tokens, cost, model, last_reason = 0, 0.0, None, ""
    for table in detected[:MAX_TABLES_MAPPED]:
        mapped = (mapper or (lambda h, r: map_rent_roll_columns(h, r, client)))(table.header, [r.cells for r in table.rows])
        tokens, cost, model = tokens + mapped.tokens, cost + mapped.cost_usd, mapped.model or model
        mapping, notes = clean_mapping(mapped.assignments, table.ncols)
        if usable_mapping(mapping):
            return ExtractionResult(build_table(table, mapping, notes), model or "", tokens, round(cost, 6))
        last_reason = f"table on page {table.header_page} ({table.ncols} columns) does not map to unit, tenant and a size or rent column"
    return ExtractionResult(RentRollExtraction(False, last_reason or "no table maps to a rent roll"), model or "", tokens, round(cost, 6))


# ---------------------------------------------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------------------------------------------

def annual_rent_used(values: dict) -> tuple[Decimal | None, str | None]:
    """The row's annual rent: as stated, else monthly x 12, else sqft x $/sf. Code derives it; the source is recorded."""
    if values.get("annual_rent") is not None:
        return values["annual_rent"], "annual"
    if values.get("monthly_rent") is not None:
        return values["monthly_rent"] * 12, "monthly x 12"
    if values.get("sqft") is not None and values.get("rent_psf") is not None:
        return values["sqft"] * values["rent_psf"], "sqft x $/sf"
    return None, None


def close(a: Decimal, b: Decimal, relative: Decimal, floor: Decimal = Decimal(1)) -> bool:
    return abs(a - b) <= max(floor, relative * max(abs(a), abs(b)))


def row_status(values: dict) -> str:
    """'vacant' or 'occupied' from the status cell and the tenant cell (a blank tenant with no rent is vacant)."""
    if values["status_text"] and is_vacant_text(values["status_text"]):
        return "vacant"
    if values["status_text"] and OCCUPIED_RE.match(values["status_text"]):
        return "occupied"
    tenant = values["tenant"] or ""
    if is_vacant_text(tenant):
        return "vacant"
    if not tenant or BLANK_RE.match(tenant):
        used, _ = annual_rent_used(values)
        return "vacant" if used is None else "occupied"
    return "occupied"


def verify_row(row: RRRow) -> None:
    v = row.values
    flags = row.flags
    if row.unparsed:
        flags.append("cell_unparsed")
    for name in ("sqft", "monthly_rent", "annual_rent", "rent_psf"):
        if v.get(name) is not None and v[name] < 0:
            flags.append("invalid_amount")
            break
    v["status"] = row_status(v)
    if v["lease_start"] and v["lease_end"] and not v["lease_start"] < v["lease_end"]:
        flags.append("bad_dates")
    tenant_is_vacancy = bool(v["tenant"]) and (is_vacant_text(v["tenant"]) or bool(BLANK_RE.match(v["tenant"])))
    status_says_vacant = bool(v["status_text"]) and is_vacant_text(v["status_text"])
    status_says_occupied = bool(v["status_text"]) and bool(OCCUPIED_RE.match(v["status_text"]))
    if v["status"] == "vacant":
        used, _ = annual_rent_used(v)
        real_tenant = bool(v["tenant"]) and not tenant_is_vacancy and not status_says_vacant  # with a status column that is a conflict, below
        if real_tenant or (used is not None and used > 0) or v["lease_start"] or v["lease_end"]:
            flags.append("vacant_row_has_data")
    if (status_says_occupied and tenant_is_vacancy) or (status_says_vacant and v["tenant"] and not tenant_is_vacancy):
        if "vacant_row_has_data" not in flags:
            flags.append("status_conflict")
    # row arithmetic: only the figures the row prints are compared
    sqft, psf, monthly, annual = v.get("sqft"), v.get("rent_psf"), v.get("monthly_rent"), v.get("annual_rent")
    mismatches = []
    if annual is not None and monthly is not None:
        mismatches.append(not close(monthly * 12, annual, MONTHLY_TOLERANCE))
    if sqft is not None and psf is not None:
        reference = annual if annual is not None else (monthly * 12 if monthly is not None else None)
        if reference is not None:
            mismatches.append(not close(sqft * psf, reference, PSF_TOLERANCE))
    if any(mismatches):
        flags.append("row_math_mismatch")


def totals_findings(ext: RentRollExtraction) -> list[dict]:
    """Rows summed against the stated totals row, for each column both have. One finding per column."""
    out = []
    if ext.totals is None:
        return out
    n = max(len(ext.rows), 1)
    for name in ("sqft", "monthly_rent", "annual_rent"):
        if name not in ext.mapping.values():
            continue
        stated = ext.totals.values.get(name)
        if stated is None:
            continue
        summed = sum((r.values[name] for r in ext.rows if r.values.get(name) is not None), Decimal(0))
        tolerance = max(Decimal(n) / 2, Decimal("0.001") * abs(stated), Decimal(1))
        out.append({"field": name, "stated": stated, "summed": summed, "difference": summed - stated, "ok": abs(summed - stated) <= tolerance})
    return out


def verify(ext: RentRollExtraction) -> None:
    for row in ext.rows:
        verify_row(row)
    if ext.suspect_columns:
        for row in ext.rows:
            row.flags.append("header_mapping_suspect")


# ---------------------------------------------------------------------------------------------------------------
# the validated fields (what is stored, shown and cited)
# ---------------------------------------------------------------------------------------------------------------

def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def row_value(row: RRRow) -> dict:
    v = row.values
    used, source = annual_rent_used(v)
    return {
        "kind": "unit", "index": row.index, "unit": v["unit"], "tenant": v["tenant"], "status": v["status"],
        "sqft": jsonable(v["sqft"]), "lease_start": jsonable(v["lease_start"]), "lease_end": jsonable(v["lease_end"]), "mtm": v["mtm"],
        "monthly_rent": jsonable(v["monthly_rent"]), "annual_rent": jsonable(v["annual_rent"]), "rent_psf": jsonable(v["rent_psf"]),
        "annual_rent_used": jsonable(used), "annual_source": source, "cells": row.cells,
    }


def _quality(pages, page_no: int) -> float:
    return next((p.quality_score for p in pages if p.page_no == page_no), 0.0)


def _field(name: str, value, quote, page_no, bbox, flags, quality, label=None) -> ValidatedField:
    passed = not (set(flags) & CHECK_FLAGS)
    # The cells come from the PDF's own text layer, positioned by code, so grounding is by construction (1.0).
    return ValidatedField(
        name, value, quote, page_no, bbox, round(field_confidence(1.0, passed, quality), 4), 1.0, list(dict.fromkeys(flags)), passed,
        [bbox] if bbox else [], label,
    )


def validate_rent_roll(ext: RentRollExtraction, pages, today: date | None = None) -> list[ValidatedField]:
    clock.today(today)
    if not ext.found:
        return [ValidatedField("table", None, None, None, None, 0.0, 0.0, ["not_found"], False, [], ext.reason)]
    verify(ext)
    findings = totals_findings(ext)
    out: list[ValidatedField] = []
    mapping_flags = ["header_mapping_suspect"] if ext.suspect_columns else []
    mapping_value = {
        "header": ext.header, "columns": [{"column": c, "header": ext.header[c], "field": f} for c, f in sorted(ext.mapping.items())],
        "psf_basis": ext.psf_basis, "notes": ext.mapping_notes, "suspect_columns": ext.suspect_columns,
        "pages": ext.pages, "subtotals": len(ext.subtotals), "ignored_notes": ext.notes[:5],
    }
    out.append(_field("mapping", mapping_value, " | ".join(ext.header), ext.header_page, ext.header_bbox, mapping_flags,
                      _quality(pages, ext.header_page or 1),
                      label=("suspect columns: " + ", ".join(ext.suspect_columns)) if ext.suspect_columns else None))
    for row in ext.rows:
        out.append(_field(f"row_{row.index}", row_value(row), " | ".join(row.cells), row.page_no, row.bbox, row.flags,
                          _quality(pages, row.page_no), label=row.label or None))
    if ext.totals is not None:
        bad = [f for f in findings if not f["ok"]]
        value = {
            "kind": "totals", "cells": ext.totals.cells,
            **{n: jsonable(ext.totals.values.get(n)) for n in ("sqft", "monthly_rent", "annual_rent")},
            "checks": [{"field": f["field"], "stated": jsonable(f["stated"]), "summed": jsonable(f["summed"]),
                        "difference": jsonable(f["difference"]), "ok": f["ok"]} for f in findings],
        }
        label = "; ".join(f"{f['field']}: rows sum to {f['summed']:,.2f}, stated {f['stated']:,.2f}" for f in bad) or None
        out.append(_field("totals", value, " | ".join(ext.totals.cells), ext.totals.page_no, ext.totals.bbox,
                          ["totals_mismatch"] if bad else [], _quality(pages, ext.totals.page_no), label=label))
    if (prop := property_field(pages)) is not None:
        out.append(prop)
    return out


# ---------------------------------------------------------------------------------------------------------------
# decide: metrics, obligations
# ---------------------------------------------------------------------------------------------------------------

def is_usable(f: ValidatedField) -> bool:
    return f.value is not None and not (set(f.flags) & BLOCKING_FLAGS)


def unit_rows(fields: list[ValidatedField]) -> list[ValidatedField]:
    return [f for f in fields if re.fullmatch(r"row_\d+", f.name) and isinstance(f.value, dict)]


def as_date(text) -> date | None:
    return date.fromisoformat(text) if text else None


def add_months(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    year, month = total // 12, total % 12 + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def years_left(lease_end: date | None, mtm: bool, today: date) -> Decimal:
    """Remaining term in years: (end - today) / 365.25, never below zero. Month-to-month and expired leases are 0."""
    if mtm or lease_end is None:
        return Decimal(0)
    return max(Decimal(0), Decimal((lease_end - today).days) / DAYS_PER_YEAR)


def build_rent_roll_brief(fields: list[ValidatedField], today: date | None = None) -> Brief:
    """Occupancy, rent, expirations and WALT, each citing the rows it was computed from. Only rows with no blocking flag count."""
    today = clock.today(today)
    brief = Brief()
    rows = unit_rows(fields)
    if not rows:
        brief.skipped.append({"key": "metrics", "reason": "no rent roll rows were read"})
        return brief
    usable = [f for f in rows if is_usable(f)]
    excluded = [f for f in rows if not is_usable(f)]
    ex_note = (f"{len(excluded)} flagged row(s) left out: " + ", ".join(f.name for f in excluded[:8]) + ("…" if len(excluded) > 8 else "")) if excluded else None
    if not usable:
        brief.skipped.append({"key": "metrics", "reason": "every row carries a blocking flag"})
        return brief
    occupied = [f for f in usable if f.value["status"] == "occupied"]
    names = lambda items: [f.name for f in items]  # noqa: E731

    def line(key, label, value, text, src, note=None):
        brief.lines.append(BriefLine(key, label, value, text, names(src), note))

    pct = lambda a, b: f"{(D(a) / D(b) * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"  # noqa: E731
    line("occupancy_units", "Occupancy by units", float(D(len(occupied)) / len(usable)),
         f"{len(occupied)} of {len(usable)} units occupied ({pct(len(occupied), len(usable))})", usable, ex_note)

    with_sqft = [f for f in usable if f.value["sqft"] is not None]
    if with_sqft:
        total_sf = sum((D(f.value["sqft"]) for f in with_sqft), Decimal(0))
        occ_sf = sum((D(f.value["sqft"]) for f in with_sqft if f.value["status"] == "occupied"), Decimal(0))
        missing = len(usable) - len(with_sqft)
        note = "; ".join(n for n in (ex_note, f"{missing} row(s) with no square footage left out" if missing else None) if n) or None
        if total_sf > 0:
            line("occupancy_sqft", "Occupancy by area", float(occ_sf / total_sf),
                 f"{fmt_sqft(occ_sf)} of {fmt_sqft(total_sf)} occupied ({pct(occ_sf, total_sf)})", with_sqft, note)
    else:
        brief.skipped.append({"key": "occupancy_sqft", "reason": "no square footage column was read"})

    rented = [f for f in occupied if f.value["annual_rent_used"] is not None]
    if rented:
        total_rent = sum((D(f.value["annual_rent_used"]) for f in rented), Decimal(0))
        no_rent = len(occupied) - len(rented)
        note = "; ".join(n for n in (ex_note, f"{no_rent} occupied row(s) show no rent" if no_rent else None) if n) or None
        line("total_annual_rent", "Total annual rent", float(money(total_rent)), f"{fmt_money(total_rent)} per year", rented, note)
        priced = [f for f in rented if f.value["sqft"] is not None and D(f.value["sqft"]) > 0]
        if priced:
            rent_p = sum((D(f.value["annual_rent_used"]) for f in priced), Decimal(0))
            sf_p = sum((D(f.value["sqft"]) for f in priced), Decimal(0))
            line("avg_rent_psf", "Average rent per sf", float(money(rent_p / sf_p)),
                 f"{fmt_money(rent_p / sf_p)} per sf per year (rent-weighted over occupied area)", priced, ex_note)
    else:
        brief.skipped.append({"key": "total_annual_rent", "reason": "no occupied row shows a rent"})

    dated = [f for f in occupied if f.value["lease_end"] or f.value["mtm"]]
    by_year: dict[int, list[ValidatedField]] = {}
    for f in occupied:
        end = as_date(f.value["lease_end"])
        if end:
            by_year.setdefault(end.year, []).append(f)
    for year in sorted(by_year):
        group = by_year[year]
        sf = sum((D(f.value["sqft"]) for f in group if f.value["sqft"] is not None), Decimal(0))
        rent = sum((D(f.value["annual_rent_used"]) for f in group if f.value["annual_rent_used"] is not None), Decimal(0))
        parts = [f"{len(group)} lease{'s' if len(group) != 1 else ''}"]
        if sf:
            parts.append(fmt_sqft(sf))
        if rent:
            parts.append(f"{fmt_whole(rent)}/yr")
        line(f"expirations_{year}", f"Expiring in {year}", len(group), " · ".join(parts), group)

    near = [f for f in occupied if (e := as_date(f.value["lease_end"])) and today < e <= add_months(today, NEAR_MONTHS)]
    two = [f for f in occupied if (e := as_date(f.value["lease_end"])) and today < e <= add_months(today, EXPIRY_HORIZON_MONTHS)]
    line("expiring_12m", "Expiring within 12 months", len(near), f"{len(near)} lease{'s' if len(near) != 1 else ''}", near)
    line("expiring_24m", "Expiring within 24 months", len(two), f"{len(two)} lease{'s' if len(two) != 1 else ''}", two)
    lapsed = [f for f in occupied if f.value["mtm"] or ((e := as_date(f.value["lease_end"])) and e <= today)]
    if lapsed:
        line("lapsed", "Expired or month-to-month", len(lapsed), f"{len(lapsed)} occupied row(s) past their end date or month-to-month; counted as zero years left", lapsed)

    for key, label, weight in (("walt_rent", "WALT by rent", "annual_rent_used"), ("walt_sqft", "WALT by area", "sqft")):
        base = [f for f in dated if f.value[weight] is not None and D(f.value[weight]) > 0]
        skipped_n = len(occupied) - len(base)
        if not base:
            brief.skipped.append({"key": key, "reason": f"no occupied row has both an end date and a {'rent' if weight != 'sqft' else 'size'}"})
            continue
        total_w = sum((D(f.value[weight]) for f in base), Decimal(0))
        walt = sum((D(f.value[weight]) * years_left(as_date(f.value["lease_end"]), f.value["mtm"], today) for f in base), Decimal(0)) / total_w
        note = "; ".join(n for n in (ex_note, f"{skipped_n} occupied row(s) without the needed figures left out" if skipped_n else None,
                                     "years left = (end date − today) / 365.25, zero if expired or month-to-month") if n)
        line(key, label, float(walt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), f"{walt.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} years", base, note)

    totals = next((f for f in fields if f.name == "totals"), None)
    if totals is not None and isinstance(totals.value, dict):
        bad = [c for c in totals.value["checks"] if not c["ok"]]
        if bad:
            text = "Rows do not add up to the stated totals: " + "; ".join(
                f"{c['field']} rows {c['summed']:,.2f} vs stated {c['stated']:,.2f}" for c in bad)
        elif totals.value["checks"]:
            text = "Rows add up to the stated totals (" + ", ".join(c["field"] for c in totals.value["checks"]) + ")"
        else:
            text = "A totals row was found but it shares no column with the rows"
        brief.lines.append(BriefLine("totals_check", "Totals check", not bad, text, ["totals"] + names(rows), None))
    else:
        brief.skipped.append({"key": "totals_check", "reason": "no totals row was found"})
    return brief


def derive_rent_roll_obligations(fields: list[ValidatedField], today: date | None = None) -> tuple[list[DerivedObligation], list[str]]:
    """An expiration for each usable occupied lease ending within the next 24 months, soonest first."""
    today = clock.today(today)
    horizon = add_months(today, EXPIRY_HORIZON_MONTHS)
    out, skipped = [], []
    rows = unit_rows(fields)
    if not rows:
        return [], ["no rent roll rows were read"]
    for f in rows:
        end = as_date(f.value.get("lease_end"))
        if f.value["status"] != "occupied" or end is None or not today < end <= horizon:
            continue
        if not is_usable(f):
            skipped.append(f"{f.name} skipped: flagged {', '.join(sorted(set(f.flags) & BLOCKING_FLAGS))}")
            continue
        who = f.value["tenant"] or f.value["unit"] or f.name
        out.append(DerivedObligation("expiration", end, f"Lease expires: {who} ({f.value['unit'] or 'unit n/a'}) on {end.isoformat()}", [f.name]))
    return sorted(out, key=lambda o: (o.due_date, o.description)), skipped


def table_flags(fields: list[ValidatedField]) -> dict[str, list[str]]:
    """Check flags present, with the fields carrying them (what the review flag reports)."""
    found: dict[str, list[str]] = {}
    for f in fields:
        for flag in f.flags:
            if flag in CHECK_FLAGS or flag in ("not_found", "month_anomaly"):
                found.setdefault(flag, []).append(f.name)
    return found
