"""Validation of hand-written label files for the held-out set (evals/heldout/{rent_roll,t12,pairs}/<stem>.json).

A label file says what a document really contains, as a person read it from the PDF. The eval scorers read these files with the
same code that reads the dev `expected.json`, so a file must have the fields they use. `validate` reports every problem it finds
(missing fields, wrong types, values outside the allowed lists) so a label can be fixed before a run; it never reads or guesses a
value from the PDF. Keys that start with an underscore are comments and are ignored.

Run it with `python scripts/check_labels.py`.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from paperbound.schemas.cre import OPEX_CATEGORIES, RENT_ROLL_FIELDS, T12_CATEGORIES

KINDS = {"rent_roll": "rent_roll", "t12": "t12", "pairs": "pair"}  # folder -> doc_type
CELL_FIELDS = tuple(f for f in RENT_ROLL_FIELDS if f != "status")
NUMBER_CELLS = ("sqft", "monthly_rent", "annual_rent", "rent_psf")
DATE_CELLS = ("lease_start", "lease_end")
RENT_ROLL_FLAGS = ("totals_mismatch",)
T12_LINE_FLAGS = ("line_sum_mismatch", "subtotal_mismatch", "noi_mismatch", "category_suspect", "month_anomaly")
PAIR_FLAGS = ("rent_income_mismatch", "property_mismatch", "vacancy_sanity")
RENT_ROLL_METRICS = ("occupancy_units", "occupancy_sqft", "total_annual_rent", "avg_rent_psf", "walt_rent_years", "walt_sqft_years", "expiring_24_months",
                     "units", "occupied_units", "total_sqft", "occupied_sqft")  # the last four are counts you may note down; they are not scored


@dataclass
class Report:
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_date(v) -> bool:
    try:
        return isinstance(v, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)) and bool(date.fromisoformat(v))
    except ValueError:
        return False


def _blank(v) -> bool:
    return v is None or (isinstance(v, (str, list, dict)) and len(v) == 0)


class _Checker:
    def __init__(self, report: Report):
        self.r = report

    def err(self, where: str, message: str) -> None:
        self.r.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.r.warnings.append(f"{where}: {message}")

    def need(self, obj: dict, key: str, where: str = "", kind=None, allow_null: bool = False):
        """The value of a required key, or None after recording why it is not acceptable."""
        at = f"{where}{key}"
        if key not in obj:
            self.err(at, "missing")
            return None
        v = obj[key]
        if v is None and allow_null:
            return v
        if _blank(v):
            self.err(at, "is empty (still the template's blank?)")
            return None
        if kind and not kind(v):
            self.err(at, f"has the wrong type or format: {v!r}"[:120])
            return None
        return v


def _common(c: _Checker, label: dict, path: Path, doc_type: str, needs_pdf: bool) -> None:
    got = c.need(label, "id", kind=lambda v: isinstance(v, str))
    if got is not None and got != path.stem:
        c.err("id", f"must be the file name without the extension ({path.stem!r}), got {got!r}")
    if label.get("doc_type") != doc_type:
        c.err("doc_type", f"must be {doc_type!r}, got {label.get('doc_type')!r}")
    c.need(label, "as_of", kind=_is_date)
    prop = c.need(label, "property", kind=lambda v: isinstance(v, dict))
    if isinstance(prop, dict):
        for key in ("name", "address"):
            if _blank(prop.get(key)):
                c.warn(f"property.{key}", "is empty; the cross-check cannot use this document's identity")
    if needs_pdf:
        pdf = c.need(label, "pdf", kind=lambda v: isinstance(v, str))
        if pdf is not None:
            if pdf != f"{path.stem}.pdf":
                c.err("pdf", f"must be {path.stem + '.pdf'!r}, got {pdf!r}")
            elif not path.with_suffix(".pdf").exists():
                c.err("pdf", f"{path.stem}.pdf is not next to this label file")


def _rent_roll(c: _Checker, label: dict, path: Path) -> None:
    _common(c, label, path, "rent_roll", True)
    headers = c.need(label, "headers", kind=lambda v: isinstance(v, list) and all(isinstance(h, str) for h in v))
    mapping = c.need(label, "mapping", kind=lambda v: isinstance(v, dict))
    mapped: set[str] = set()
    if isinstance(mapping, dict):
        for col, field_name in mapping.items():
            if not str(col).isdigit():
                c.err(f"mapping.{col}", "the key must be a column number (0 is the first column)")
            elif headers is not None and int(col) >= len(headers):
                c.err(f"mapping.{col}", f"there are only {len(headers)} headers")
            if field_name not in RENT_ROLL_FIELDS:
                c.err(f"mapping.{col}", f"{field_name!r} is not one of {', '.join(RENT_ROLL_FIELDS)}")
            else:
                mapped.add(field_name)
        if not mapped & {"unit", "tenant"} or not mapped & set(NUMBER_CELLS):
            c.err("mapping", "needs a unit or tenant column and at least one size or rent column")
    rows = c.need(label, "rows", kind=lambda v: isinstance(v, list))
    for i, row in enumerate(rows or []):
        at = f"rows[{i}]."
        if not isinstance(row, dict):
            c.err(at[:-1], "must be an object with page and cells")
            continue
        page = row.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            c.err(at + "page", "is missing or not a page number (1 is the first page)")
        cells = row.get("cells")
        if not isinstance(cells, dict):
            c.err(at + "cells", "missing")
            continue
        for name in sorted(mapped & set(CELL_FIELDS)):
            if name not in cells:
                c.err(at + f"cells.{name}", "missing (use null for a cell that is empty)")
        for name, v in cells.items():
            if name not in CELL_FIELDS:
                c.err(at + f"cells.{name}", f"is not a cell field ({', '.join(CELL_FIELDS)})")
            elif v is not None and name in NUMBER_CELLS and not _is_number(v):
                c.err(at + f"cells.{name}", f"must be a number (or null), got {v!r}")
            elif v is not None and name in DATE_CELLS and not _is_date(v):
                c.err(at + f"cells.{name}", f"must be an ISO date YYYY-MM-DD (or null), got {v!r}")
            elif v is not None and name in ("unit", "tenant") and not isinstance(v, str):
                c.err(at + f"cells.{name}", f"must be text (or null), got {v!r}")
    totals = label.get("totals")
    if not isinstance(totals, dict) or not totals:
        c.warn("totals", "not labeled: the totals check will be scored as 'no totals row'")
    else:
        for k, v in totals.items():
            if k not in ("sqft", "monthly_rent", "annual_rent"):
                c.err(f"totals.{k}", "is not sqft, monthly_rent or annual_rent")
            elif v is not None and not _is_number(v):
                c.err(f"totals.{k}", f"must be a number (or null), got {v!r}")
    flags = label.get("expected_flags")
    if not isinstance(flags, list):
        c.err("expected_flags", "missing (use [] if nothing should be flagged, or [\"totals_mismatch\"] if the stated totals really are wrong)")
    else:
        for f in flags:
            if f not in RENT_ROLL_FLAGS:
                c.err("expected_flags", f"{f!r} is not one of {', '.join(RENT_ROLL_FLAGS)}")
    metrics = label.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        c.warn("metrics", "not labeled: occupancy, rent and WALT will not be scored for this document")
    else:
        for k, v in metrics.items():
            if k not in RENT_ROLL_METRICS and k != "expirations_by_year":
                c.err(f"metrics.{k}", f"unknown metric (known: {', '.join(RENT_ROLL_METRICS)}, expirations_by_year)")
            elif k == "expirations_by_year":
                if not isinstance(v, dict) or not all(re.fullmatch(r"\d{4}", str(y)) and isinstance(e, dict) and "count" in e for y, e in v.items()):
                    c.err("metrics.expirations_by_year", 'must look like {"2027": {"count": 3}}')
            elif v is not None and not _is_number(v):
                c.err(f"metrics.{k}", f"must be a number (or null), got {v!r}")


def _t12(c: _Checker, label: dict, path: Path) -> None:
    _common(c, label, path, "t12", True)
    months = c.need(label, "months", kind=lambda v: isinstance(v, list) and all(isinstance(m, str) and m for m in v))
    if months is not None and len(months) != 12:
        c.err("months", f"must list exactly 12 month headers as printed, got {len(months)}")
    c.need(label, "total_header", kind=lambda v: isinstance(v, str), allow_null=True)
    lines = c.need(label, "lines", kind=lambda v: isinstance(v, list))
    labels = set()
    for i, ln in enumerate(lines or []):
        at = f"lines[{i}]."
        if not isinstance(ln, dict):
            c.err(at[:-1], "must be an object")
            continue
        text = c.need(ln, "label", at, kind=lambda v: isinstance(v, str))
        labels.add(text)
        if ln.get("kind") not in ("line", "subtotal"):
            c.err(at + "kind", "must be 'line' or 'subtotal'")
        if ln.get("category") not in T12_CATEGORIES:
            c.err(at + "category", f"must be one of {', '.join(T12_CATEGORIES)}")
        values = ln.get("months")
        if not isinstance(values, list) or len(values) != 12 or not all(v is None or _is_number(v) for v in values):
            c.err(at + "months", "must be 12 numbers as printed (null for an empty cell)")
        if "total" not in ln or not (ln["total"] is None or _is_number(ln["total"])):
            c.err(at + "total", "must be a number as printed (null if the line has no total)")
        page = ln.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            c.err(at + "page", "is missing or not a page number")
    flags = label.get("expected_flags")
    if not isinstance(flags, dict):
        c.err("expected_flags", 'missing (use {} if nothing should be flagged; otherwise {"Net Operating Income": ["noi_mismatch"]})')
    else:
        for name, fl in flags.items():
            if name not in labels:
                c.err(f"expected_flags.{name}", "is not the label of any line")
            for f in fl if isinstance(fl, list) else [fl]:
                if f not in T12_LINE_FLAGS:
                    c.err(f"expected_flags.{name}", f"{f!r} is not one of {', '.join(T12_LINE_FLAGS)}")
    anomalies = label.get("expected_anomalies")
    if not isinstance(anomalies, list):
        c.err("expected_anomalies", "missing (use [] if no month is more than 25% from its line's median)")
    else:
        for a in anomalies:
            if not isinstance(a, dict) or a.get("label") not in labels or (months and a.get("month") not in months):
                c.err("expected_anomalies", f"each entry needs a line label and a month header as printed: {a!r}"[:140])
    expected = label.get("expected")
    if not isinstance(expected, dict) or not expected:
        c.warn("expected", "not labeled: EGI, expenses, NOI and the monthly trend will not be scored for this document")
    else:
        for k in ("egi", "opex", "noi", "expense_ratio"):
            if expected.get(k) is not None and not _is_number(expected[k]):
                c.err(f"expected.{k}", "must be a number")
        trend = expected.get("noi_monthly")
        if trend is not None and (not isinstance(trend, list) or len(trend) != 12 or not all(_is_number(v) for v in trend)):
            c.err("expected.noi_monthly", "must be 12 numbers")
        for cat, v in (expected.get("opex_by_category") or {}).items():
            if cat not in OPEX_CATEGORIES or not _is_number(v):
                c.err(f"expected.opex_by_category.{cat}", f"needs an operating expense category ({', '.join(OPEX_CATEGORIES)}) and a number")


def _pair(c: _Checker, label: dict, path: Path) -> None:
    _common(c, label, path, "pair", False)
    root = path.parent.parent
    for key, folder in (("rent_roll", "rent_roll"), ("t12", "t12")):
        stem = c.need(label, key, kind=lambda v: isinstance(v, str))
        if stem is not None and not ((root / folder / f"{stem}.json").exists() and (root / folder / f"{stem}.pdf").exists()):
            c.err(key, f"{folder}/{stem}.pdf and {folder}/{stem}.json must both exist")
    tol = label.get("tolerance")
    if not _is_number(tol) or not 0 < tol <= 1:
        c.err("tolerance", "must be a share between 0 and 1 (0.05 is 5%)")
    flags = label.get("expected_flags")
    if not isinstance(flags, list):
        c.err("expected_flags", "missing (use [] if the two documents should agree)")
    else:
        for f in flags:
            if f not in PAIR_FLAGS:
                c.err("expected_flags", f"{f!r} is not one of {', '.join(PAIR_FLAGS)}")
    for key in ("rent_roll_total_annual_rent", "t12_base_rent", "difference", "difference_share"):
        if not _is_number(label.get(key)):
            c.err(key, "must be a number (work it out from the two documents)")
    if all(_is_number(label.get(k)) for k in ("rent_roll_total_annual_rent", "t12_base_rent", "difference")):
        if abs(label["t12_base_rent"] - label["rent_roll_total_annual_rent"] - label["difference"]) > 0.02:
            c.err("difference", "is not t12_base_rent minus rent_roll_total_annual_rent")


CHECKS = {"rent_roll": _rent_roll, "t12": _t12, "pairs": _pair}


def validate(path: Path, kind: str | None = None) -> Report:
    """Problems with one label file. `kind` is rent_roll, t12 or pairs; by default the name of the folder it is in."""
    path = Path(path)
    kind = kind or path.parent.name
    report = Report(str(path))
    if kind not in CHECKS:
        report.errors.append(f"cannot tell what kind of label this is (the folder should be rent_roll, t12 or pairs, not {kind!r})")
        return report
    try:
        label = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.errors.append(f"cannot read it as JSON: {exc}")
        return report
    if not isinstance(label, dict):
        report.errors.append("the file must contain one JSON object")
        return report
    CHECKS[kind](_Checker(report), {k: v for k, v in label.items() if not k.startswith("_")}, path)
    return report


def find_labels(root: Path) -> list[tuple[str, Path]]:
    """(kind, label file) for every <stem>.json under root/{rent_roll,t12,pairs}."""
    return [(kind, p) for kind in CHECKS for p in sorted((Path(root) / kind).glob("*.json"))]
