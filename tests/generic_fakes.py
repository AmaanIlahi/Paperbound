"""A capital call notice (a type Paperbound has no schema for) and a fake extraction whose quotes ground in its text."""

import io
from functools import lru_cache

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from paperbound.pipeline.extract import ExtractionResult
from paperbound.pipeline.parse import parse_pdf
from paperbound.schemas.generic import FallbackAmount, FallbackDate, FallbackFields, FallbackObligation, FallbackParty

LINES = [
    "CAPITAL CALL NOTICE",
    'Northgate Growth Fund II, L.P. (the "Fund") calls capital from Juniper Family Office LLC (the "Investor").',
    "Notice date: October 1, 2026",
    "Capital call amount: $250,000.00 USD",
    "Please wire the capital call amount to the Fund account by November 15, 2026.",
    "Management fee of $12,500.00 was settled on September 1, 2026.",
]


def notice_pdf(lines=LINES) -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    for i, line in enumerate(lines):
        pdf.drawString(72, 700 - 24 * i, line)
    pdf.save()
    return buf.getvalue()


@lru_cache(maxsize=None)
def notice_pages():
    return parse_pdf(notice_pdf())


def fallback(**over) -> FallbackFields:
    base = {
        "parties": [
            FallbackParty(name="Northgate Growth Fund II, L.P.", role="issuer", source_quote="Northgate Growth Fund II, L.P.", page=1),
            FallbackParty(name="Juniper Family Office LLC", role="investor", source_quote="capital from Juniper Family Office LLC", page=1),
        ],
        "dates": [
            FallbackDate(value="2026-10-01", label="notice date", source_quote="Notice date: October 1, 2026", page=1),
            FallbackDate(value="2026-11-15", label="payment due date", source_quote="to the Fund account by November 15, 2026", page=1),
            FallbackDate(value="2026-09-01", label="fee settled", source_quote="was settled on September 1, 2026", page=1),
        ],
        "amounts": [
            FallbackAmount(value=250000.0, currency="USD", label="capital call amount", source_quote="Capital call amount: $250,000.00 USD", page=1),
        ],
        "obligations": [
            FallbackObligation(
                description="Wire the capital call amount to the Fund account", due_date="2026-11-15",
                source_quote="Please wire the capital call amount to the Fund account by November 15, 2026.", page=1),
        ],
    }
    base.update(over)
    return FallbackFields(**base)


def fake_generic_extractor(pages, extracted=None):
    return ExtractionResult(extracted or fallback(), "gpt-4o-mini", 1200, 0.0004)


def empty_fallback() -> FallbackFields:
    return FallbackFields(parties=[], dates=[], amounts=[], obligations=[])
