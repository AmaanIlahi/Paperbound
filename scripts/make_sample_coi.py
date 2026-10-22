"""Generate fixtures/demo/insurance_certificate.pdf (a fictional certificate of liability insurance) and its
expected JSON. Producer, insurers and policy numbers are made up."""

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "demo"
OUT = DEMO / "insurance_certificate.pdf"
EXPECTED = DEMO / "insurance_certificate.expected.json"

tiny = ParagraphStyle("tiny", fontName="Helvetica", fontSize=6.5, leading=8)
small = ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10)
label = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=6.5, leading=8, textColor=colors.HexColor("#444444"))
cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10)
title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=14, leading=17)

PRODUCER = "Northgate Risk Partners LLC"
INSURED = "Brightline Studio Inc."
HOLDER = "Harborview Properties LLC"

POLICIES = [
    {
        "letter": "A", "type": "COMMERCIAL GENERAL LIABILITY", "type_expected": "General Liability",
        "insurer": "Meridian Mutual Insurance Company", "number": "GL-5521904",
        "effective": "01/15/2026", "expiration": "01/15/2027",
        "limits": [
            ("EACH OCCURRENCE", 1000000), ("DAMAGE TO RENTED PREMISES (Ea occurrence)", 100000),
            ("MED EXP (Any one person)", 5000), ("PERSONAL & ADV INJURY", 1000000),
            ("GENERAL AGGREGATE", 2000000), ("PRODUCTS - COMP/OP AGG", 2000000),
        ],
        "each": 1000000, "aggregate": 2000000,
    },
    {
        "letter": "B", "type": "UMBRELLA LIAB", "type_expected": "Umbrella Liability",
        "insurer": "Pinecrest Casualty Company", "number": "UM-7730418",
        "effective": "03/01/2026", "expiration": "03/01/2027",
        "limits": [("EACH OCCURRENCE", 5000000), ("AGGREGATE", 5000000)],
        "each": 5000000, "aggregate": 5000000,
    },
]


def money(n: int) -> str:
    return f"${n:,.0f}"


def iso(us_date: str) -> str:
    m, d, y = us_date.split("/")
    return f"{y}-{m}-{d}"


def build():
    story = [
        Table([[Paragraph("CERTIFICATE OF LIABILITY INSURANCE", title), Paragraph("DATE (MM/DD/YYYY)<br/><b>01/10/2026</b>", small)]],
              colWidths=[5.2 * inch, 1.9 * inch]),
        Spacer(1, 4),
        Paragraph(
            "THIS CERTIFICATE IS ISSUED AS A MATTER OF INFORMATION ONLY AND CONFERS NO RIGHTS UPON THE CERTIFICATE "
            "HOLDER. THIS CERTIFICATE DOES NOT AFFIRMATIVELY OR NEGATIVELY AMEND, EXTEND OR ALTER THE COVERAGE "
            "AFFORDED BY THE POLICIES BELOW. THIS CERTIFICATE OF INSURANCE DOES NOT CONSTITUTE A CONTRACT BETWEEN "
            "THE ISSUING INSURER(S), AUTHORIZED REPRESENTATIVE OR PRODUCER, AND THE CERTIFICATE HOLDER.",
            tiny,
        ),
        Spacer(1, 6),
    ]
    head = Table(
        [
            [Paragraph("PRODUCER", label), Paragraph("INSURERS AFFORDING COVERAGE", label)],
            [
                Paragraph(f"<b>{PRODUCER}</b><br/>400 Market Street, Suite 900<br/>Philadelphia, PA 19106<br/>"
                          "Contact: Dana Reyes, (215) 555-0148", cell),
                Paragraph("INSURER A: Meridian Mutual Insurance Company NAIC # 90210<br/>"
                          "INSURER B: Pinecrest Casualty Company NAIC # 90211", cell),
            ],
            [Paragraph("INSURED", label), ""],
            [Paragraph(f"<b>{INSURED}</b><br/>Suite 410, 220 Hudson Street<br/>New York, NY 10013", cell), ""],
        ],
        colWidths=[3.4 * inch, 3.7 * inch],
    )
    head.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("SPAN", (1, 2), (1, 3))]))
    story += [head, Spacer(1, 8), Paragraph("COVERAGES &nbsp; CERTIFICATE NUMBER: 26-0417 &nbsp; REVISION NUMBER: 1", small), Spacer(1, 3)]

    rows = [[Paragraph(t, label) for t in ("LTR", "TYPE OF INSURANCE", "POLICY NUMBER", "POLICY EFF (MM/DD/YYYY)", "POLICY EXP (MM/DD/YYYY)", "LIMITS")]]
    for p in POLICIES:
        limits = [Paragraph(f"{name} &nbsp; {money(v)}", cell) for name, v in p["limits"]]
        rows.append([
            Paragraph(p["letter"], cell), Paragraph(p["type"], cell), Paragraph(p["number"], cell),
            Paragraph(p["effective"], cell), Paragraph(p["expiration"], cell), limits,
        ])
    coverage = Table(rows, colWidths=[0.35 * inch, 1.5 * inch, 1.0 * inch, 0.9 * inch, 0.9 * inch, 2.45 * inch])
    coverage.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee"))]))
    story += [coverage, Spacer(1, 8)]

    story += [
        Paragraph("DESCRIPTION OF OPERATIONS / LOCATIONS / VEHICLES", label),
        Paragraph(
            f"{HOLDER} is included as an additional insured with respect to general liability arising out of the "
            "insured's operations at Suite 410, 220 Hudson Street, New York, NY 10013. Coverage is primary and "
            "non-contributory where required by written contract.", cell),
        Spacer(1, 8),
    ]
    foot = Table(
        [
            [Paragraph("CERTIFICATE HOLDER", label), Paragraph("CANCELLATION", label)],
            [
                Paragraph(f"<b>{HOLDER}</b><br/>1 Water Street, Suite 1200<br/>New York, NY 10004", cell),
                Paragraph("SHOULD ANY OF THE ABOVE DESCRIBED POLICIES BE CANCELLED BEFORE THE EXPIRATION DATE THEREOF, "
                          "NOTICE WILL BE DELIVERED IN ACCORDANCE WITH THE POLICY PROVISIONS.<br/><br/>"
                          "AUTHORIZED REPRESENTATIVE: Dana Reyes", tiny),
            ],
        ],
        colWidths=[3.4 * inch, 3.7 * inch],
    )
    foot.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                              ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [foot, Spacer(1, 10), Paragraph("ACORD-style sample certificate. Fictional parties and policy numbers.", tiny)]
    return story


def expected() -> dict:
    return {
        "doc_type": "insurance_certificate",
        "as_of": "2026-10-22",
        "pdfs": ["insurance_certificate.pdf"],
        "insured": INSURED,
        "certificate_holder": HOLDER,
        "producer": PRODUCER,
        "policies": [
            {
                "type_contains": p["type_expected"].split()[0].casefold(),
                "insurer": p["insurer"],
                "number": p["number"],
                "effective_date": iso(p["effective"]),
                "expiration_date": iso(p["expiration"]),
                "each_occurrence_limit": float(p["each"]),
                "aggregate_limit": float(p["aggregate"]),
            }
            for p in POLICIES
        ],
        "min_each_occurrence": 1000000,
        "earliest_expiration": "2027-01-15",
        "reminder_lead_days": [60, 30, 14],
        "tracker_status": "renewal needed",
    }


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(OUT), pagesize=LETTER, title="Certificate of Liability Insurance", author=PRODUCER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
    ).build(build())
    EXPECTED.write_text(json.dumps(expected(), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}\nwrote {EXPECTED}")


if __name__ == "__main__":
    main()
