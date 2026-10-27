"""T-12 operating statement (architecture section 4.10): Extract (table, months and total column by code, one labelling
call), Verify, Decide.

The model's only job is `mapping.map_t12_labels`: which category each line label belongs to. The month columns and the
total column are read from the header by code, every cell is parsed by code, and every sum, subtotal check, NOI, ratio and
anomaly is arithmetic on parsed values. Nothing is guessed: if no table with twelve month columns is found, the run says
so and asks for a review.

Sign conventions (code, not the model): a vacancy or credit loss always reduces income, whichever sign the statement
prints it with, so it enters as minus its absolute value; operating expenses enter as their absolute value; every other
revenue line keeps the sign printed. Items below the line (capex, reserves, debt service, depreciation) are not part of NOI.

Fields stored for a run: `columns` (the months, the total column, how the table was read) and `line_N` (one per line,
subtotals included, each with its category, monthly values, stated and computed total).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Callable

from paperbound import clock
from paperbound.pipeline import tables as tables_mod
from paperbound.pipeline.extract import ExtractionResult
from paperbound.pipeline.impact import Brief, BriefLine
from paperbound.pipeline.mapping import MappingResult, map_t12_labels
from paperbound.pipeline.rent_roll import BLANK_RE, UNPARSED, D, fmt_money, fmt_whole, is_usable, money, parse_number
from paperbound.pipeline.types import DerivedObligation, ValidatedField
from paperbound.pipeline.validate import BLOCKING_FLAGS, CHECK_FLAGS, field_confidence, squash
from paperbound.schemas.cre import BELOW_LINE_CATEGORIES, OPEX_CATEGORIES, REVENUE_CATEGORIES, T12_CATEGORIES

N_MONTHS = 12
ANOMALY_SHARE = Decimal("0.25")  # a month more than 25% away from the line's median
MIN_MONTHS_FOR_MEDIAN = 6
ROUNDING_UNITS = (Decimal(1000), Decimal(100), Decimal(10), Decimal(1))
TOLERANCE_UNITS = Decimal("6.5")  # figures rounded to a unit: twelve months plus the total can differ by up to 6.5 units

_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_TEXT = re.compile(r"^\s*(" + r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?" + r")\.?[\s\-'/,]*((?:20)?\d{2})?\s*$", re.I)
_MONTH_NUM = re.compile(r"^\s*(\d{1,2})\s*[/-]\s*((?:20)?\d{2})\s*$")
_MONTH_ISO = re.compile(r"^\s*(\d{4})\s*[-/]\s*(\d{1,2})\s*$")
_TOTAL_HEADER = re.compile(r"^\W*(?:total|ttm|t[\s-]?12|trailing\b.*|annual|annualized|12[\s-]*mo\w*(?:\s*total)?|year|sum|ytd)\b", re.I)
BELOW_LINE_RE = re.compile(
    r"capital|\bcap\s?ex\b|\bcapx\b|reserve|replacement|debt\s+service|mortgage|principal|interest\s+expense|loan|depreciation|amortization"
    r"|tenant\s+improvement|\bTIs?\b|leasing\s+commission|\bLCs?\b", re.I)
EGI_RE = re.compile(r"effective\s+gross|\begi\b", re.I)
TOTAL_INCOME_RE = re.compile(r"^\W*total\s+(?:gross\s+|operating\s+)?(?:income|revenues?)\b|^\W*(?:gross\s+)?(?:income|revenues?)\s+total\b", re.I)
TOTAL_OPEX_RE = re.compile(r"total\s+(?:operating\s+)?(?:expenses?|costs?)|total\s+opex|operating\s+expenses?\s+total", re.I)
NOI_RE = re.compile(r"net\s+operating\s+income|^\W*noi\b|\bnoi\W*$", re.I)


def parse_month_header(text: str) -> tuple[int, int | None] | None:
    """(month 1-12, year or None) for a header such as Jan-26, Oct 2025, October, 10/2025 or 2025-10; None otherwise."""
    text = squash(text).strip()
    m = _MONTH_TEXT.match(text)
    if m:
        year = m.group(2)
        return _MONTH_NAMES.index(m.group(1).lower()[:3]) + 1, (int(year) + (2000 if len(year) == 2 else 0)) if year else None
    m = _MONTH_NUM.match(text)
    if m and 1 <= int(m.group(1)) <= 12:
        year = m.group(2)
        return int(m.group(1)), int(year) + (2000 if len(year) == 2 else 0)
    m = _MONTH_ISO.match(text)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(2)), int(m.group(1))
    return None


@dataclass
class T12Line:
    index: int
    page_no: int
    bbox: list[float]
    cells: list[str]
    label: str
    section: str
    months: list[Decimal | None] = field(default_factory=list)
    total_stated: Decimal | None = None
    category: str | None = None
    unparsed: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"line_{self.index}"


@dataclass
class T12Extraction:
    found: bool
    reason: str = ""
    months: list[str] = field(default_factory=list)  # the header text of each month column, in printed order
    month_cols: list[int] = field(default_factory=list)
    total_col: int | None = None
    label_col: int = 0
    header: list[str] = field(default_factory=list)
    header_page: int | None = None
    header_bbox: list[float] | None = None
    lines: list[T12Line] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mapping_notes: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    tolerance: Decimal = Decimal(1)  # set by verify(): how far two figures that should be equal may differ

    def count(self) -> int:
        return len(self.lines)

    def summary(self) -> str:
        if not self.found:
            return f"no T-12 table ({self.reason})"
        return f"{len(self.lines)} line(s), {len(self.months)} month columns" + (", total column found" if self.total_col is not None else ", no total column")

    def explain(self) -> str:
        if not self.found:
            return f"Code looked for a table with twelve month columns and found none: {self.reason}. Nothing is guessed; the run asks for a review."
        return (f"Code found the table with PyMuPDF (page(s) {', '.join(map(str, self.pages))}), read the month columns "
                f"({self.months[0]} to {self.months[-1]}) and the total column from the header, and parsed every cell. "
                "The model only said which category each line label belongs to.")


# ---------------------------------------------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------------------------------------------

def month_columns(header: list[str]) -> list[int]:
    return [i for i, h in enumerate(header) if parse_month_header(h)]


def total_column(header: list[str], month_cols: list[int]) -> int | None:
    found = [i for i, h in enumerate(header) if i not in month_cols and _TOTAL_HEADER.match(squash(h).strip())]
    return found[0] if found else None


def label_column(header: list[str], rows: list[list[str]], taken: set[int]) -> int:
    """The first column that is not a month or the total and holds text (an account-code column of digits is skipped)."""
    for i in range(len(header)):
        if i in taken:
            continue
        cells = [r[i] for r in rows if i < len(r) and r[i].strip()]
        if cells and sum(bool(re.search(r"[A-Za-z]", c)) for c in cells) / len(cells) >= 0.6:
            return i
    return next((i for i in range(len(header)) if i not in taken), 0)


def section_for(table: tables_mod.Table, row_index: int) -> str:
    """The most recent heading above a data row: a spanning note that precedes it, short and without digits."""
    best = ""
    for text, at in zip(table.notes, table.note_at):
        if at <= row_index and len(text) <= 60 and not re.search(r"\d", text):
            best = text
    return best


def build_lines(table: tables_mod.Table, month_cols: list[int], total_col: int | None, label_col: int) -> list[T12Line]:
    lines: list[T12Line] = []
    for pos, tr in enumerate(table.rows):
        label = squash(tr.cells[label_col] if label_col < len(tr.cells) else "").strip()
        cells = [tr.cells[c] if c < len(tr.cells) else "" for c in month_cols]
        total_cell = tr.cells[total_col] if total_col is not None and total_col < len(tr.cells) else ""
        if not label or not any(c.strip() for c in [*cells, total_cell]):
            continue
        line = T12Line(len(lines) + 1, tr.page_no, tr.bbox, tr.cells, label[:80], section_for(table, pos))
        for col, cell in zip(month_cols, cells):
            parsed = parse_number(cell)
            if parsed is UNPARSED:
                line.unparsed.append(table.header[col])
                parsed = None
            line.months.append(parsed)
        if total_col is not None:
            parsed = parse_number(total_cell)
            if parsed is UNPARSED:
                line.unparsed.append("total")
                parsed = None
            line.total_stated = parsed
        lines.append(line)
    return lines


def extract_t12(pages, pdf: bytes | None, today=None, mapper: Callable | None = None, client=None) -> ExtractionResult:
    """Find the T-12 table, read its month and total columns in code, ask the model to categorise the line labels.

    `mapper(labels)` returns a MappingResult of line number -> category (tests inject one)."""
    if not pdf:
        return ExtractionResult(T12Extraction(False, "the PDF file is not available, so its tables cannot be detected"), "", 0, 0.0)
    candidates = []
    for table in tables_mod.extract_tables(pdf):
        cols = month_columns(table.header)
        if len(cols) >= N_MONTHS - 0 and len(table.rows) >= 3:
            candidates.append((table, cols))
    if not candidates:
        return ExtractionResult(T12Extraction(False, "no table with twelve month columns was found in the PDF"), "", 0, 0.0)
    table, cols = max(candidates, key=lambda tc: len(tc[0].rows))
    cols = cols[:N_MONTHS] if len(cols) > N_MONTHS else cols
    if len(cols) != N_MONTHS:
        return ExtractionResult(T12Extraction(False, f"the table has {len(cols)} month columns, not {N_MONTHS}"), "", 0, 0.0)
    total_col = total_column(table.header, cols)
    taken = {*cols, *([total_col] if total_col is not None else [])}
    label_col = label_column(table.header, [r.cells for r in table.rows], taken)
    lines = build_lines(table, cols, total_col, label_col)
    if len(lines) < 3:
        return ExtractionResult(T12Extraction(False, "the table has fewer than three line items"), "", 0, 0.0)
    ext = T12Extraction(
        True, months=[table.header[c] for c in cols], month_cols=cols, total_col=total_col, label_col=label_col, header=table.header,
        header_page=table.header_page, header_bbox=table.header_bbox, lines=lines, notes=list(table.notes), pages=list(table.pages),
    )
    labels = [(ln.label, ln.section) for ln in lines]
    mapped = (mapper or (lambda items: map_t12_labels(items, client)))(labels)
    for i, ln in enumerate(lines):
        category = mapped.assignments.get(i)
        ln.category = category if category in T12_CATEGORIES else None
    ext.mapping_notes = list(mapped.notes)
    return ExtractionResult(ext, mapped.model or "", mapped.tokens, mapped.cost_usd)


# ---------------------------------------------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------------------------------------------

def zero(values: list[Decimal | None]) -> list[Decimal]:
    return [v if v is not None else Decimal(0) for v in values]


def month_sum(lines: list[T12Line], how: Callable[[Decimal], Decimal] = lambda v: v) -> list[Decimal]:
    out = [Decimal(0)] * N_MONTHS
    for ln in lines:
        for i, v in enumerate(zero(ln.months)[:N_MONTHS]):
            out[i] += how(v)
    return out


def statement(lines: list[T12Line]) -> dict[str, list[Decimal]]:
    """Monthly revenue, expense and NOI series from the categorised lines, with the sign rules in the module docstring."""
    revenue = [ln for ln in lines if ln.category in ("base_rent", "recoveries", "other_income")]
    vacancy = [ln for ln in lines if ln.category == "vacancy_credit_loss"]
    opex = [ln for ln in lines if ln.category in OPEX_CATEGORIES]
    gross = month_sum(revenue)
    loss = month_sum(vacancy, lambda v: -abs(v))
    egi = [a + b for a, b in zip(gross, loss)]
    expenses = month_sum(opex, abs)
    return {"gross_income": gross, "egi": egi, "opex": expenses, "noi": [a - b for a, b in zip(egi, expenses)]}


def rounding_unit(lines: list[T12Line]) -> Decimal:
    """The unit the statement is rounded to: 1000 if every figure is a multiple of 1,000, then 100, 10, 1; 0.01 if any has cents."""
    values = [v for ln in lines for v in [*ln.months, ln.total_stated] if v is not None and v != 0]
    if any(v != v.to_integral_value() for v in values):
        return Decimal("0.01")
    return next((u for u in ROUNDING_UNITS if all(v % u == 0 for v in values)), Decimal(1))


def tolerance_for(unit: Decimal) -> Decimal:
    """How far apart two figures that should be equal may be: the rounding of the figures they are made of, never a share of
    their size (a $900 error on a $1.2M line is still an error)."""
    return max(Decimal(1), TOLERANCE_UNITS * unit)


def compare_series(ln: T12Line, expected: list[Decimal], tol: Decimal) -> list[str]:
    """Months (and the total column) where a stated subtotal line differs from the computed series."""
    bad = []
    for i, want in enumerate(expected):
        have = ln.months[i] if i < len(ln.months) else None
        if have is not None and abs(have - want) > tol:
            bad.append(f"month {i + 1}: stated {have:,.2f}, computed {want:,.2f}")
    if ln.total_stated is not None and abs(ln.total_stated - sum(expected, Decimal(0))) > tol:
        bad.append(f"total: stated {ln.total_stated:,.2f}, computed {sum(expected, Decimal(0)):,.2f}")
    return bad


def verify(ext: T12Extraction) -> None:
    ext.tolerance = tol = tolerance_for(rounding_unit(ext.lines))
    for ln in ext.lines:
        if ln.unparsed:
            ln.flags.append("cell_unparsed")
        if ln.category is None:
            ln.flags.append("category_suspect")
            ln.notes.append("the model returned no category for this line")
        elif ln.category in OPEX_CATEGORIES and BELOW_LINE_RE.search(ln.label):
            ln.flags.append("category_suspect")
            ln.notes.append("the label reads like an item below the line but it was counted as an operating expense")
        stated_any = any(v is not None for v in ln.months)
        if ln.total_stated is not None and stated_any:
            computed = sum(zero(ln.months), Decimal(0))
            if abs(ln.total_stated - computed) > tol:
                ln.flags.append("line_sum_mismatch")
                ln.notes.append(f"months add up to {computed:,.2f} but the total column says {ln.total_stated:,.2f}")
    series = statement(ext.lines)
    for ln in ext.lines:
        if ln.category != "subtotal":
            continue
        want, flag = None, "subtotal_mismatch"
        if NOI_RE.search(ln.label):
            want, flag = series["noi"], "noi_mismatch"
        elif EGI_RE.search(ln.label):
            want = series["egi"]
        elif TOTAL_OPEX_RE.search(ln.label):
            want = series["opex"]
        elif TOTAL_INCOME_RE.search(ln.label):
            bad_net = compare_series(ln, series["egi"], tol)
            bad_gross = compare_series(ln, series["gross_income"], tol)
            if bad_net and bad_gross:  # it matches neither the net nor the gross income: report the net one
                ln.flags.append(flag)
                ln.notes += bad_net[:3]
            continue
        if want is None:
            continue
        bad = compare_series(ln, want, tol)
        if bad:
            ln.flags.append(flag)
            ln.notes += bad[:3]
    for ln in ext.lines:
        if ln.category in REVENUE_CATEGORIES + OPEX_CATEGORIES:
            values = [v for v in ln.months if v is not None]
            if len(values) >= MIN_MONTHS_FOR_MEDIAN:
                mid = D(median(values))
                if mid != 0:
                    for i, v in enumerate(ln.months):
                        if v is not None and abs(v - mid) > ANOMALY_SHARE * abs(mid):
                            ln.anomalies.append({"month": ext.months[i], "value": float(v), "median": float(mid),
                                                 "deviation": float(((v - mid) / abs(mid)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))})
                    if ln.anomalies:
                        ln.flags.append("month_anomaly")


# ---------------------------------------------------------------------------------------------------------------
# the validated fields
# ---------------------------------------------------------------------------------------------------------------

def line_value(ln: T12Line) -> dict:
    return {
        "kind": "line", "index": ln.index, "label": ln.label, "section": ln.section, "category": ln.category,
        "months": [float(v) if v is not None else None for v in ln.months],
        "total_stated": float(ln.total_stated) if ln.total_stated is not None else None,
        "total_computed": float(sum(zero(ln.months), Decimal(0))), "anomalies": ln.anomalies, "notes": ln.notes, "cells": ln.cells,
    }


def validate_t12(ext: T12Extraction, pages, today: date | None = None) -> list[ValidatedField]:
    clock.today(today)
    if not ext.found:
        return [ValidatedField("table", None, None, None, None, 0.0, 0.0, ["not_found"], False, [], ext.reason)]
    verify(ext)
    quality = {p.page_no: p.quality_score for p in pages}
    out = [ValidatedField(
        "columns",
        {"months": ext.months, "total_column": ext.header[ext.total_col] if ext.total_col is not None else None,
         "label_column": ext.header[ext.label_col], "tolerance": float(ext.tolerance), "pages": ext.pages, "notes": ext.mapping_notes, "ignored_notes": ext.notes[:8]},
        " | ".join(ext.header), ext.header_page, ext.header_bbox, 1.0, 1.0, [], True, [ext.header_bbox] if ext.header_bbox else [], None,
    )]
    for ln in ext.lines:
        passed = not (set(ln.flags) & CHECK_FLAGS)
        label = ln.label + (f" ({ln.category})" if ln.category else "")
        out.append(ValidatedField(
            ln.name, line_value(ln), " | ".join(ln.cells), ln.page_no, ln.bbox,
            round(field_confidence(1.0, passed, quality.get(ln.page_no, 0.0)), 4), 1.0, list(dict.fromkeys(ln.flags)), passed, [ln.bbox],
            "; ".join(ln.notes) if ln.notes else label,
        ))
    return out


# ---------------------------------------------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------------------------------------------

def line_fields(fields: list[ValidatedField]) -> list[ValidatedField]:
    return [f for f in fields if re.fullmatch(r"line_\d+", f.name) and isinstance(f.value, dict)]


def to_t12(f: ValidatedField) -> T12Line:
    v = f.value
    ln = T12Line(v["index"], f.page_no or 0, f.bbox or [], v.get("cells", []), v["label"], v.get("section", ""),
                 [D(x) if x is not None else None for x in v["months"]], D(v["total_stated"]) if v["total_stated"] is not None else None,
                 v["category"], flags=list(f.flags))
    return ln


def fmt_pct(x: Decimal) -> str:
    return f"{(x * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def join_notes(*parts: str | None) -> str | None:
    return "; ".join(p for p in parts if p) or None


def build_t12_brief(fields: list[ValidatedField], today: date | None = None) -> Brief:
    """EGI, operating expenses, NOI (computed, and stated if present), expense ratio, expenses by category, the monthly NOI
    trend and the month anomalies. Only lines with no blocking flag are summed; the note says how many were left out."""
    clock.today(today)
    brief = Brief()
    all_fields = line_fields(fields)
    if not all_fields:
        brief.skipped.append({"key": "metrics", "reason": "no statement lines were read"})
        return brief
    columns = next((f.value for f in fields if f.name == "columns" and isinstance(f.value, dict)), {"months": [f"m{i + 1}" for i in range(N_MONTHS)]})
    months = columns["months"]
    usable = [f for f in all_fields if is_usable(f)]
    left_out = [f for f in all_fields if not is_usable(f) and f.value["category"] != "subtotal"]
    note = (f"{len(left_out)} flagged line(s) left out: " + ", ".join(f.name for f in left_out[:6])) if left_out else None
    lines = [(f, to_t12(f)) for f in usable]
    series = statement([ln for _, ln in lines])
    by_cat: dict[str, list[tuple[ValidatedField, T12Line]]] = {}
    for f, ln in lines:
        by_cat.setdefault(ln.category or "", []).append((f, ln))
    names = lambda items: [f.name for f, _ in items]  # noqa: E731
    revenue_items = [x for c in REVENUE_CATEGORIES for x in by_cat.get(c, [])]
    opex_items = [x for c in OPEX_CATEGORIES for x in by_cat.get(c, [])]
    total = lambda s: sum(s, Decimal(0))  # noqa: E731
    egi, opex, noi = total(series["egi"]), total(series["opex"]), total(series["noi"])

    def add(key, label, value, text, src, note_=None):
        brief.lines.append(BriefLine(key, label, value, text, src, note_))

    if not revenue_items:
        brief.skipped.append({"key": "egi", "reason": "no revenue line survived the checks"})
    else:
        add("egi", "Effective gross income", float(money(egi)), fmt_money(egi), names(revenue_items), join_notes("revenue lines summed over the twelve months, vacancy and credit loss deducted", note))
    if not opex_items:
        brief.skipped.append({"key": "total_opex", "reason": "no operating expense line survived the checks"})
    else:
        add("total_opex", "Operating expenses", float(money(opex)), fmt_money(opex), names(opex_items), join_notes("operating expense lines summed over the twelve months; items below the line are not included", note))
    if revenue_items and opex_items:
        add("noi", "Net operating income (computed)", float(money(noi)), fmt_money(noi),
            names(revenue_items) + names(opex_items), join_notes("revenue minus operating expenses; items below the line are excluded", note))
        if egi > 0:
            add("expense_ratio", "Expense ratio", float((opex / egi).quantize(Decimal("0.0001"))), fmt_pct(opex / egi),
                names(revenue_items) + names(opex_items), join_notes("operating expenses as a share of effective gross income", note))
    stated = [f for f in all_fields if f.value["category"] == "subtotal" and NOI_RE.search(f.value["label"])]
    for f in stated[:1]:
        ln = to_t12(f)
        stated_total = ln.total_stated if ln.total_stated is not None else total(zero(ln.months))
        gap = (money(noi) - money(stated_total)) if revenue_items and opex_items else None
        text = f"{fmt_money(stated_total)} as printed" + ("" if gap is None else (" (agrees with the computed NOI)" if abs(gap) <= D(columns.get("tolerance", 1)) * N_MONTHS and "noi_mismatch" not in f.flags
                                                                                    else f" (differs from the computed NOI by {fmt_money(abs(gap))})"))
        add("noi_stated", "Net operating income (stated)", float(money(stated_total)), text, [f.name])
    for category in OPEX_CATEGORIES:
        items = by_cat.get(category, [])
        if items:
            cat_total = total(month_sum([ln for _, ln in items], abs))
            share = f" · {fmt_pct(cat_total / opex)} of operating expenses" if opex else ""
            add(f"opex_{category}", category.replace("_", " ").capitalize(), float(money(cat_total)), f"{fmt_money(cat_total)}{share}", names(items))
    below = [x for c in BELOW_LINE_CATEGORIES for x in by_cat.get(c, [])]
    if below:
        add("below_line", "Below the line (not in NOI)", float(money(total(month_sum([ln for _, ln in below], abs)))),
            f"{fmt_money(total(month_sum([ln for _, ln in below], abs)))} in {', '.join(sorted({ln.category for _, ln in below}))}", names(below))
    if revenue_items and opex_items:
        trend = series["noi"]
        hi, lo = max(range(len(trend)), key=lambda i: trend[i]), min(range(len(trend)), key=lambda i: trend[i])
        add("noi_trend", "Monthly NOI", [float(money(v)) for v in trend],
            f"{months[0]} {fmt_whole(trend[0])} → {months[-1]} {fmt_whole(trend[-1])}; high {fmt_whole(trend[hi])} ({months[hi]}), low {fmt_whole(trend[lo])} ({months[lo]})",
            names(revenue_items) + names(opex_items), note)
    flagged = [f for f in all_fields if f.value.get("anomalies")]
    count = sum(len(f.value["anomalies"]) for f in flagged)
    if flagged:
        worst = sorted(((a, f) for f in flagged for a in f.value["anomalies"]), key=lambda x: -abs(x[0]["deviation"]))[:3]
        add("anomalies", "Months far from the line's median", count,
            f"{count} month(s) in {len(flagged)} line(s) are more than 25% from the line's median; largest: "
            + "; ".join(f"{f.value['label']} {a['month']} ({a['deviation'] * 100:+.0f}%)" for a, f in worst), [f.name for f in flagged])
    else:
        add("anomalies", "Months far from the line's median", 0, "No month is more than 25% from its line's median", [f.name for f in usable if f.value["category"] in REVENUE_CATEGORIES + OPEX_CATEGORIES][:50])
    problems = [f for f in all_fields if set(f.flags) & {"subtotal_mismatch", "noi_mismatch", "line_sum_mismatch"}]
    if problems:
        add("checks", "Statement checks", False, "Does not check out: " + "; ".join(f"{f.value['label']} ({', '.join(sorted(set(f.flags) & {'subtotal_mismatch', 'noi_mismatch', 'line_sum_mismatch'}))})" for f in problems[:4]),
            [f.name for f in problems])
    else:
        add("checks", "Statement checks", True, "Every line's months add up to its total, and the stated subtotals and NOI agree with the lines", [f.name for f in all_fields][:50])
    return brief


def derive_t12_obligations(fields: list[ValidatedField], today: date | None = None) -> tuple[list[DerivedObligation], list[str]]:
    """A T-12 states no dates, so it creates no obligations."""
    return [], ["a T-12 operating statement carries no deadlines"]
