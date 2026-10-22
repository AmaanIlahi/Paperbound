"""Shared helpers: the demo certificate parsed once, and a fake extraction whose quotes ground in its real text."""

from functools import lru_cache
from pathlib import Path

from paperbound.pipeline.extract import ExtractionResult
from paperbound.pipeline.parse import parse_pdf
from paperbound.schemas.insurance import InsuranceCertFields

DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "demo"


@lru_cache(maxsize=None)
def pages_of(name: str):
    return parse_pdf((DEMO / name).read_bytes())


def f(value, quote, page=1):
    return {"value": value, "source_quote": quote, "page": page}


def policy(**over):
    base = {
        "type": f("General Liability", "COMMERCIAL GENERAL LIABILITY"),
        "insurer": f("Meridian Mutual Insurance Company", "INSURER A: Meridian Mutual Insurance Company NAIC # 90210"),
        "number": f("GL-5521904", "GL-5521904"),
        "effective_date": f("2026-01-15", "01/15/2026"),
        "expiration_date": f("2027-01-15", "01/15/2027"),
        "each_occurrence_limit": f(1000000.0, "EACH OCCURRENCE $1,000,000"),
        "aggregate_limit": f(2000000.0, "GENERAL AGGREGATE $2,000,000"),
    }
    base.update(over)
    return base


def umbrella(**over):
    base = {
        "type": f("Umbrella Liability", "UMBRELLA LIAB"),
        "insurer": f("Pinecrest Casualty Company", "INSURER B: Pinecrest Casualty Company NAIC # 90211"),
        "number": f("UM-7730418", "UM-7730418"),
        "effective_date": f("2026-03-01", "03/01/2026"),
        "expiration_date": f("2027-03-01", "03/01/2027"),
        "each_occurrence_limit": f(5000000.0, "EACH OCCURRENCE $5,000,000"),
        "aggregate_limit": f(5000000.0, "AGGREGATE $5,000,000"),
    }
    base.update(over)
    return base


def cert(policies=None, **over) -> InsuranceCertFields:
    data = {
        "insured": f("Brightline Studio Inc.", "Brightline Studio Inc."),
        "certificate_holder": f("Harborview Properties LLC", "CERTIFICATE HOLDER CANCELLATION Harborview Properties LLC"),
        "producer": f("Northgate Risk Partners LLC", "PRODUCER INSURERS AFFORDING COVERAGE Northgate Risk Partners LLC"),
        "policies": policies if policies is not None else [policy(), umbrella()],
    }
    data.update(over)
    return InsuranceCertFields.model_validate(data)


def fake_coi_extractor(pages):
    return ExtractionResult(cert(), "fake-model", 10, 0.0)
