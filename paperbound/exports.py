"""CSV exports of the tabular types, built from the stored fields. Only rows with no blocking flag are written.

Text from a document is untrusted: a cell that starts with = + - @ (or a tab or carriage return) would run as a formula in
a spreadsheet, so such a cell is written with a leading apostrophe.
"""

import csv
import io
import re

from paperbound.pipeline.types import ValidatedField
from paperbound.pipeline.validate import BLOCKING_FLAGS

_FORMULA = re.compile(r"^[=+\-@\t\r]")


def safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if _FORMULA.match(text) else text


def _clean(f: ValidatedField) -> bool:
    return f.value is not None and not (set(f.flags) & BLOCKING_FLAGS)


def rent_roll_csv(fields: list[ValidatedField]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["row", "page", "unit", "tenant", "status", "sqft", "lease_start", "lease_end", "monthly_rent", "annual_rent",
                     "rent_psf", "annual_rent_used", "annual_rent_source", "table_warning"])
    totals = next((f for f in fields if f.name == "totals"), None)
    warning = "totals_mismatch" if totals is not None and "totals_mismatch" in totals.flags else ""
    for f in fields:
        if not (f.name.startswith("row_") and isinstance(f.value, dict) and _clean(f)):
            continue
        v = f.value
        writer.writerow([safe(f.name), f.page_no, safe(v["unit"]), safe(v["tenant"]), v["status"], v["sqft"], v["lease_start"], v["lease_end"],
                         v["monthly_rent"], v["annual_rent"], v["rent_psf"], v["annual_rent_used"], safe(v["annual_source"]), warning])
    return out.getvalue()


def t12_csv(fields: list[ValidatedField]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    months = next((f.value["months"] for f in fields if f.name == "columns" and isinstance(f.value, dict)), [])
    writer.writerow(["line", "page", "label", "category", "section", *[safe(m) for m in months], "total_stated", "total_computed"])
    for f in fields:
        if not (f.name.startswith("line_") and isinstance(f.value, dict) and _clean(f)):
            continue
        v = f.value
        writer.writerow([safe(f.name), f.page_no, safe(v["label"]), v["category"], safe(v.get("section")), *(v["months"] or [None] * len(months)),
                         v["total_stated"], v["total_computed"]])
    return out.getvalue()


EXPORTERS = {"rent_roll": rent_roll_csv, "t12": t12_csv}
