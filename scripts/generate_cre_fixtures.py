"""Generate the synthetic DEV fixtures for the commercial real estate pack: rent rolls, T-12 operating statements and
rent roll + T-12 pairs.

    python scripts/generate_cre_fixtures.py            # writes evals/fixtures/{rent_roll,t12,pairs}/<id>/
    python scripts/generate_cre_fixtures.py --check    # regenerate into a temp dir and fail if anything differs

DEV ONLY. These documents are template-generated and regular; they were used while building and tuning the rent roll and
T-12 code, so they say nothing about accuracy on real documents. The held-out set (evals/heldout/, git-ignored, filled in
by hand from real offering memoranda) is the only honest measure, and nothing is tuned against it.

Seeded and reproducible. Every expected value is written from the values the document was rendered from, with its own
arithmetic (Decimal), never read back from the pipeline. Names and addresses are made up.
"""

import argparse
import io
import json
import random
import sys
import tempfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "evals" / "fixtures"
SEED = 20261101
AS_OF = date(2026, 10, 22)

PROPERTIES = [
    ("Harborview Commons", "1200 Harbor Street, Baltimore, MD 21202"),
    ("Cedar Point Plaza", "455 Cedar Point Road, Columbus, OH 43215"),
    ("Mill Creek Center", "88 Mill Creek Parkway, Raleigh, NC 27601"),
    ("Westgate Office Park", "2300 Westgate Drive, Tempe, AZ 85281"),
    ("Lakeshore Square", "710 Lakeshore Boulevard, Chicago, IL 60611"),
]
TENANTS = [
    "Northwind Dental", "Blue Heron Cafe", "Summit Legal Group", "Apex Fitness", "Juniper Pediatrics", "Granite Title",
    "Orchard Pharmacy", "Brightpath Tutoring", "Keystone Insurance", "Lumen Optical", "Redwood Realty", "Tidewater Bank",
    "Maple Street Bakery", "Pioneer Engineering", "Silverline Salon", "Atlas Staffing", "Harbor Physical Therapy",
    "Crescent Wireless", "Evergreen Accounting", "Meridian Imaging", "Birchwood Pet Care", "Copperleaf Yoga",
    "Falcon Security", "Ironbridge Capital", "Zephyr Travel", "Oakline Architects", "Starlight Cinema Cafe",
    "Delta Print & Ship", "Quarry Hill Brewing", "Vantage Consulting", "Willow Learning Center", "Beacon Credit Union",
    "Foxglove Florist", "Ridgeway Surgical", "Sable Coffee Roasters", "Tern Software", "Nimbus Cloud Services",
    "Paragon Dry Cleaners", "Linden Wealth", "Cobalt Labs",
]


def q2(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def usd(x, sign: bool = True) -> str:
    return f"{'$' if sign else ''}{q2(x):,.2f}"


def fmt_date(d: date, style: str) -> str:
    if style == "us":
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    if style == "iso":
        return d.isoformat()
    if style == "mon":
        return f"{d:%b} {d.day}, {d.year}"
    if style == "us2":  # two-digit year
        return f"{d.month}/{d.day}/{d.year % 100:02d}"
    raise ValueError(style)


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


# ----------------------------------------------------------------------------------------------------------------
# rent roll: data model and the independent arithmetic used for the expected values
# ----------------------------------------------------------------------------------------------------------------

RR_LAYOUTS = {
    # layout: columns [(header, field)], date style, grid lines, money sign, text shown for a vacant tenant
    "A": dict(
        cols=[("Suite", "unit"), ("Tenant", "tenant"), ("Sq Ft", "sqft"), ("Lease Start", "lease_start"), ("Lease End", "lease_end"),
              ("Monthly Rent", "monthly_rent"), ("Annual Rent", "annual_rent"), ("Rent $/SF", "rent_psf")],
        dates="us", grid=True, sign=True, vacant_text="VACANT",
    ),
    "B": dict(
        cols=[("Tenant", "tenant"), ("Unit", "unit"), ("RSF", "sqft"), ("Commencement", "lease_start"), ("Expiration", "lease_end"),
              ("Annual $/SF", "rent_psf"), ("Status", "status")],
        dates="mon", grid=True, sign=True, vacant_text="-",
    ),
    "C": dict(
        cols=[("Space", "unit"), ("Occupant", "tenant"), ("Area (SF)", "sqft"), ("Start", "lease_start"), ("Expires", "lease_end"),
              ("Rent / Mo", "monthly_rent"), ("Rent / Yr", "annual_rent")],
        dates="iso", grid=False, sign=False, vacant_text="Vacant",
    ),
    "D": dict(
        cols=[("Unit #", "unit"), ("Tenant Name", "tenant"), ("Leased Area", "sqft"), ("Term Begin", "lease_start"), ("Term End", "lease_end"),
              ("Base Rent (Monthly)", "monthly_rent")],
        dates="us2", grid=False, sign=True, vacant_text="Available",
    ),
}

# (layout, units, plant a totals mismatch)
RR_PLAN = [
    ("A", 14, False), ("A", 38, False), ("A", 22, True), ("B", 16, False), ("B", 30, False),
    ("C", 36, False), ("C", 44, False), ("C", 20, False), ("D", 18, False), ("D", 34, False),
]


def make_units(rng: random.Random, n: int, layout: str) -> list[dict]:
    tenants = rng.sample(TENANTS, min(n, len(TENANTS)))
    units = []
    for i in range(n):
        sqft = rng.randrange(800, 12000, 10)
        vacant = rng.random() < 0.17
        unit = {"A": f"Suite {100 + 10 * i}", "B": f"{1 + i // 8}{chr(65 + i % 8)}", "C": f"S-{i + 1:03d}", "D": f"{101 + i}"}[layout]
        row = {"unit": unit, "sqft": sqft, "tenant": None, "lease_start": None, "lease_end": None, "psf": None,
               "monthly_rent": None, "annual_rent": None, "status": "vacant" if vacant else "occupied"}
        if not vacant:
            start = AS_OF - timedelta(days=rng.randrange(120, 365 * 7))
            end = add_years(start, rng.choice([3, 5, 5, 7, 10])) - timedelta(days=1)
            if rng.random() < 0.3:  # make sure a good share expire inside the next 24 months
                end = AS_OF + timedelta(days=rng.randrange(20, 700))
                start = add_years(end, -rng.choice([3, 5])) + timedelta(days=1)
            psf = Decimal(rng.randrange(1800, 4800)) / 100
            row.update(tenant=tenants[i % len(tenants)], lease_start=start, lease_end=end, psf=psf)
            if layout == "D":  # monthly is the stated rent; annual follows from it
                row["monthly_rent"] = q2(psf * sqft / 12)
                row["annual_rent"] = row["monthly_rent"] * 12
                row["psf"] = None
            else:
                row["annual_rent"] = q2(psf * sqft)
                row["monthly_rent"] = q2(row["annual_rent"] / 12)
        units.append(row)
    return units


def rr_metrics(units: list[dict], as_of: date = AS_OF) -> dict:
    """Expected metrics, from the true unit values, with arithmetic independent of the pipeline."""
    occ = [u for u in units if u["status"] == "occupied"]
    total_sqft = sum(u["sqft"] for u in units)
    occ_sqft = sum(u["sqft"] for u in occ)
    annual = sum((u["annual_rent"] for u in occ), Decimal(0))

    def years(u):
        return max(Decimal(0), Decimal((u["lease_end"] - as_of).days) / Decimal("365.25"))

    walt_rent = sum((u["annual_rent"] * years(u) for u in occ), Decimal(0)) / annual if annual else Decimal(0)
    walt_sqft = sum((Decimal(u["sqft"]) * years(u) for u in occ), Decimal(0)) / occ_sqft if occ_sqft else Decimal(0)
    by_year: dict[str, dict] = {}
    for u in occ:
        y = by_year.setdefault(str(u["lease_end"].year), {"count": 0, "sqft": 0, "annual_rent": Decimal(0)})
        y["count"] += 1
        y["sqft"] += u["sqft"]
        y["annual_rent"] += u["annual_rent"]
    horizon = date(as_of.year + 2, as_of.month, min(as_of.day, 28))
    return {
        "units": len(units), "occupied_units": len(occ),
        "occupancy_units": float(Decimal(len(occ)) / len(units)),
        "total_sqft": total_sqft, "occupied_sqft": occ_sqft, "occupancy_sqft": float(Decimal(occ_sqft) / total_sqft),
        "total_annual_rent": float(annual), "avg_rent_psf": float(annual / occ_sqft) if occ_sqft else None,
        "walt_rent_years": float(walt_rent), "walt_sqft_years": float(walt_sqft),
        "expirations_by_year": {y: {"count": v["count"], "sqft": v["sqft"], "annual_rent": float(v["annual_rent"])} for y, v in sorted(by_year.items())},
        "expiring_24_months": sum(1 for u in occ if as_of < u["lease_end"] <= horizon),
    }


def render_rent_roll(layout: str, prop: tuple[str, str], units: list[dict], planted: bool):
    """(pdf bytes, header list, displayed body rows, stated totals). The totals row sits last in the table."""
    spec = RR_LAYOUTS[layout]
    cols = spec["cols"]
    fields = [f for _, f in cols]
    headers = [h for h, _ in cols]

    def cell(u: dict, f: str):
        if u["status"] == "vacant" and f not in ("unit", "sqft", "tenant", "status"):
            return ""
        if f == "unit":
            return u["unit"]
        if f == "tenant":
            return u["tenant"] if u["status"] == "occupied" else spec["vacant_text"]
        if f == "sqft":
            return f"{u['sqft']:,}"
        if f == "lease_start":
            return fmt_date(u["lease_start"], spec["dates"])
        if f == "lease_end":
            return fmt_date(u["lease_end"], spec["dates"])
        if f == "monthly_rent":
            return usd(u["monthly_rent"], spec["sign"])
        if f == "annual_rent":
            return usd(u["annual_rent"], spec["sign"])
        if f == "rent_psf":
            return usd(u["psf"], spec["sign"])
        if f == "status":
            return "Occupied" if u["status"] == "occupied" else "Vacant"
        raise KeyError(f)

    body = [[cell(u, f) for f in fields] for u in units]
    occ = [u for u in units if u["status"] == "occupied"]
    totals: dict[str, Decimal | int] = {}
    if "sqft" in fields:
        totals["sqft"] = sum(u["sqft"] for u in units)
    if "monthly_rent" in fields:
        totals["monthly_rent"] = sum((u["monthly_rent"] for u in occ), Decimal(0))
    if "annual_rent" in fields:
        totals["annual_rent"] = sum((u["annual_rent"] for u in occ), Decimal(0))
    if planted:  # the stated total is off by a round figure; no row is wrong
        key = "annual_rent" if "annual_rent" in totals else "sqft"
        totals[key] = totals[key] + (Decimal(12000) if key == "annual_rent" else 450)
    total_row, label_done = [], False
    for f in fields:
        if f == "sqft":
            total_row.append(f"{totals['sqft']:,}")
        elif f in ("monthly_rent", "annual_rent"):
            total_row.append(usd(totals[f], spec["sign"]))
        elif not label_done and f in ("unit", "tenant"):
            total_row.append("Total")
            label_done = True
        else:
            total_row.append("")

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    buf = io.BytesIO()
    pagesize = landscape(LETTER)
    doc = SimpleDocTemplate(buf, pagesize=pagesize, invariant=1, leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.7 * inch, bottomMargin=0.6 * inch)
    story = [
        Paragraph(f"<b>{prop[0]}</b>", styles["Title"]),
        Paragraph(f"{prop[1]}", styles["Normal"]),
        Paragraph(f"Rent Roll as of {AS_OF.month:02d}/{AS_OF.day:02d}/{AS_OF.year}", styles["Heading3"]),
        Spacer(1, 8),
    ]
    header_row = headers if layout != "D" else [Paragraph(f"<b>{h}</b>", ParagraphStyle("h", parent=small, textColor=colors.white)) for h in headers]
    data = [header_row] + body + [total_row]
    width = pagesize[0] - 1.0 * inch
    weights = {"unit": 0.9, "tenant": 2.2, "sqft": 0.9, "lease_start": 1.1, "lease_end": 1.1, "monthly_rent": 1.2, "annual_rent": 1.3, "rent_psf": 0.9, "status": 0.9}
    ws = [weights[f] for f in fields]
    col_w = [width * w / sum(ws) for w in ws]
    table = Table(data, colWidths=col_w, repeatRows=1)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, f in enumerate(fields):
        if f in ("sqft", "monthly_rent", "annual_rent", "rent_psf"):
            style.append(("ALIGN", (i, 0), (i, -1), "RIGHT"))
    if spec["grid"]:
        style += [("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6"))]
    elif layout == "D":
        style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        for r in range(1, len(data) - 1):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f0f3f7")))
    else:
        style += [("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 10), Paragraph("Figures are as reported by property management and are unaudited.", small)]

    def footer(canvas, d):
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.5 * inch, 0.35 * inch, f"{prop[0]} - rent roll - page {d.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue(), headers, body, total_row, totals


def pages_of(pdf: bytes, keys: list[str]) -> list[int]:
    """Page of each key, in order, found in the rendered text."""
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as d:
        texts = [p.get_text() for p in d]
    out, last = [], 0
    for key in keys:
        for pi in range(last, len(texts)):
            if key in texts[pi]:
                last = pi
                break
        out.append(last + 1)
    return out


def page_count(pdf: bytes) -> int:
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as d:
        return len(d)


def build_rent_roll(layout: str, n_units: int, planted: bool, rng: random.Random, prop: tuple[str, str], units=None) -> tuple[bytes, dict]:
    units = units or make_units(rng, n_units, layout)
    pdf, headers, body, total_row, totals = render_rent_roll(layout, prop, units, planted)
    spec = RR_LAYOUTS[layout]
    fields = [f for _, f in spec["cols"]]
    pages = pages_of(pdf, [row[0] if row[0] else row[1] for row in body])
    rows = []
    for u, page in zip(units, pages):
        shown = {
            "unit": u["unit"], "sqft": u["sqft"], "tenant": u["tenant"],
            "lease_start": u["lease_start"].isoformat() if u["lease_start"] else None,
            "lease_end": u["lease_end"].isoformat() if u["lease_end"] else None,
            "monthly_rent": float(u["monthly_rent"]) if u["monthly_rent"] is not None else None,
            "annual_rent": float(u["annual_rent"]) if u["annual_rent"] is not None else None,
            "rent_psf": float(u["psf"]) if u["psf"] is not None else None,
        }
        rows.append({
            "page": page, "status": u["status"],
            "cells": {f: shown[f] for f in fields if f != "status"},
            "true_annual_rent": float(u["annual_rent"]) if u["annual_rent"] is not None else None,
        })
    truth = {
        "layout": layout, "pages": page_count(pdf), "headers": headers,
        "mapping": {str(i): f for i, f in enumerate(fields)},
        "rows": rows,
        "totals": {k: float(v) for k, v in totals.items()},
        "totals_row": total_row,
        "planted": {"totals_mismatch": planted},
        "expected_flags": ["totals_mismatch"] if planted else [],
        "metrics": rr_metrics(units),
    }
    return pdf, truth


# ----------------------------------------------------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------------------------------------------------

def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")


def write_rent_rolls(out: Path) -> list[dict]:
    rng = random.Random(SEED)
    made = []
    for i, (layout, n_units, planted) in enumerate(RR_PLAN, start=1):
        prop = PROPERTIES[(i - 1) % len(PROPERTIES)]
        pdf, truth = build_rent_roll(layout, n_units, planted, rng, prop)
        folder = out / "rent_roll" / f"rr_{i:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "doc.pdf").write_bytes(pdf)
        dump(folder / "expected.json", {"id": f"rr_{i:03d}", "doc_type": "rent_roll", "dev_only": True, "pdf": "doc.pdf",
                                          "as_of": AS_OF.isoformat(), "property": {"name": prop[0], "address": prop[1]}, **truth})
        made.append({"id": f"rr_{i:03d}", "layout": layout, "pages": truth["pages"]})
    return made


def generate(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    made = write_rent_rolls(out)
    print(f"rent rolls: {len(made)} ->", ", ".join(f"{m['id']}[{m['layout']},{m['pages']}p]" for m in made))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="regenerate into a temp dir and fail if the committed fixtures differ")
    args = parser.parse_args(argv)
    if not args.check:
        generate(OUT_DIR)
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        generate(Path(tmp))
        bad = []
        for path in sorted(Path(tmp).rglob("expected.json")):
            rel = path.relative_to(tmp)
            if not (OUT_DIR / rel).exists() or (OUT_DIR / rel).read_text(encoding="utf-8") != path.read_text(encoding="utf-8"):
                bad.append(str(rel))
        if bad:
            print("fixtures differ:", bad)
            return 1
    print("fixtures match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
