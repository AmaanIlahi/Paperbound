"""A quote must be about the field it supports (anchor words in the quote or its sentence), and a date field holds a
concrete date, never words about one. Both are blocking flags; generic fields have no schema and no anchors."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from paperbound.pipeline.obligations import derive_obligations
from paperbound.pipeline.parse import parse_pdf
from paperbound.pipeline.validate import (
    BLOCKING_FLAGS,
    CONTEXT_CONFIDENCE_CAP,
    context_ok,
    is_described_date,
    quote_sentence,
    validate_coi,
    validate_fields,
    validate_generic,
)
from paperbound.schemas.generic import FallbackAmount, FallbackDate, FallbackFields
from paperbound.schemas.insurance import COI_ANCHORS
from paperbound.schemas.lease import LEASE_ANCHORS, LeaseFields
from tests.coi_fixture import cert, f, pages_of, policy

TODAY = date(2026, 10, 22)
LEASE_015 = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "lease" / "lease_015" / "doc.pdf"
RENEWAL_RENT = "Renewal rent shall be ninety-five percent (95%) of the then-prevailing market rate"


def page(text, page_no=1):
    return SimpleNamespace(
        page_no=page_no, text=text, quality_score=1.0,
        words=[{"text": w, "bbox": [i * 10, 0, i * 10 + 9, 10]} for i, w in enumerate(text.split())],
    )


def lease(**fields):
    data = {name: {"value": None, "source_quote": None, "page": None} for name in LeaseFields.model_fields}
    data.update({k: {"value": v[0], "source_quote": v[1], "page": v[2]} for k, v in fields.items()})
    return LeaseFields.model_validate(data)


def by_name(fields):
    return {x.name: x for x in fields}


# ---- the lease_015 case: a renewal-rent percentage offered as the holdover rate ----

def test_lease_015_renewal_rent_is_not_accepted_as_the_holdover_rate():
    pages = parse_pdf(LEASE_015.read_bytes())
    wrong = lease(holdover_rate_percent=(95.0, RENEWAL_RENT, 2))
    field = by_name(validate_fields(wrong, pages, TODAY))["holdover_rate_percent"]
    assert field.value == 95.0 and "ungrounded" not in field.flags  # the quote is real and the number is in it...
    assert "field_context_mismatch" in field.flags  # ...but it is about renewal rent
    assert "field_context_mismatch" in BLOCKING_FLAGS and field.confidence <= CONTEXT_CONFIDENCE_CAP


def test_a_flagged_field_never_reaches_the_obligations():
    text = "The term ends on March 31, 2027. Landlord shall complete repairs within sixty (60) days."
    fields = validate_fields(lease(
        end_date=("2027-03-31", "The term ends on March 31, 2027", 1),
        notice_period_days=(60, "Landlord shall complete repairs within sixty (60) days", 1)), [page(text)], TODAY)
    notice = by_name(fields)["notice_period_days"]
    assert "field_context_mismatch" in notice.flags and by_name(fields)["end_date"].flags == []
    obligations, skipped = derive_obligations(fields)
    assert obligations == [] and any("notice_period_days" in reason for reason in skipped)


@pytest.mark.parametrize("text, quote", [
    ("If Tenant remains in possession after the term ends, rent shall be 150% of the Base Rent.", "rent shall be 150% of the Base Rent"),
    ("Holdover. Tenant shall pay one hundred fifty percent (150%) of the Base Rent.", "one hundred fifty percent (150%) of the Base Rent"),
    ("Any hold-over by Tenant is at 150% of the Base Rent.", "at 150% of the Base Rent"),
    ("Should the tenant continue to hold over, the rate is 150%.", "the rate is 150%"),
])
def test_holdover_wording_in_the_quote_or_its_sentence_passes(text, quote):
    field = by_name(validate_fields(lease(holdover_rate_percent=(150.0, quote, 1)), [page(text)], TODAY))["holdover_rate_percent"]
    assert "field_context_mismatch" not in field.flags


def test_the_anchor_may_be_in_the_sentence_and_not_the_quote():
    text = "If Tenant is holding over, it pays 150% of Base Rent."
    field = by_name(validate_fields(lease(holdover_rate_percent=(150.0, "it pays 150% of Base Rent", 1)), [page(text)], TODAY))
    assert "field_context_mismatch" not in field["holdover_rate_percent"].flags


def test_a_neighbouring_sentence_does_not_count():
    text = "Holdover applies after expiration. Renewal rent shall be ninety-five percent (95%) of the market rate."
    assert quote_sentence(RENEWAL_RENT, text).startswith("Renewal rent")
    assert not context_ok("holdover_rate_percent", RENEWAL_RENT, text, LEASE_ANCHORS)


def test_every_typed_field_has_anchors():
    assert set(LEASE_ANCHORS) == set(LeaseFields.model_fields)
    from paperbound.schemas.insurance import InsuranceCertFields, PolicyFields

    assert set(COI_ANCHORS) == (set(InsuranceCertFields.model_fields) - {"policies"}) | set(PolicyFields.model_fields)
    assert all(terms for terms in [*LEASE_ANCHORS.values(), *COI_ANCHORS.values()])


def test_the_demo_certificate_has_no_context_flags_and_an_unrelated_figure_has_one():
    clean = validate_coi(cert(), pages_of("insurance_certificate.pdf"), TODAY)
    assert all("field_context_mismatch" not in x.flags for x in clean)
    text = "Certificate holder is shown below. A late filing fee of $5,000 applies to any amendment requested after issue."
    wrong = by_name(validate_coi(cert([policy(aggregate_limit=f(5000.0, "late filing fee of $5,000", 1))]), [page(text)], TODAY))
    assert "field_context_mismatch" in wrong["policy_1.aggregate_limit"].flags


# ---- generic fields are exempt: no schema, no anchors ----

def test_generic_fields_have_no_anchor_check():
    text = "Capital call notice. Please pay $517,000.00 by March 10, 2020."
    extracted = FallbackFields(
        parties=[], obligations=[],
        dates=[FallbackDate(value="2020-03-10", label="due date", source_quote="by March 10, 2020", page=1)],
        amounts=[FallbackAmount(value=517000.0, currency="USD", label="holdover rate", source_quote="pay $517,000.00", page=1)],
    )
    assert all("field_context_mismatch" not in x.flags for x in validate_generic(extracted, [page(text)], TODAY))


# ---- date_not_concrete ----

@pytest.mark.parametrize("value, quote, expected", [
    ("the date of the last signature", "as of the date of the last signature", True),
    ("three (3) years after the Effective Date", "for a term of three (3) years after the Effective Date", True),
    ("upon execution", "effective upon execution of this Agreement", True),
    ("2020-03-10", "ten days after the Effective Date", True),  # a date the quote does not state, only describes
    ("2020-03-10", "Due Date March 10, 2020", False),
    ("2004-03-19", "entered into as of the 19 day of March, 2004", False),  # an ordinal date form, not a description
    ("2026-13-45", "March 10, 2020", False),  # malformed digits are invalid_date, not words
])
def test_described_dates_are_recognised(value, quote, expected):
    assert is_described_date(value, quote) is expected


def test_a_lease_date_given_as_words_is_blocked_and_never_guessed():
    p = page("This Agreement takes effect on the date of the last signature below. The term ends on March 31, 2027.")
    got = by_name(validate_fields(lease(
        start_date=("the date of the last signature", "takes effect on the date of the last signature below", 1),
        end_date=("2027-03-31", "The term ends on March 31, 2027", 1)), [p], TODAY))
    start = got["start_date"]
    assert "date_not_concrete" in start.flags and "invalid_date" not in start.flags
    assert start.value == "the date of the last signature"  # kept as the model wrote it; no date is derived
    assert "date_not_concrete" in BLOCKING_FLAGS and not start.validators_passed
    assert got["end_date"].flags == []


def test_an_iso_date_whose_quote_only_describes_the_date_is_blocked():
    p = page("This Agreement takes effect on the date of the last signature below.")
    start = by_name(validate_fields(lease(start_date=("2024-04-01", "takes effect on the date of the last signature below", 1)), [p], TODAY))["start_date"]
    assert {"date_not_concrete", "value_not_in_quote"} <= set(start.flags)


def test_generic_and_insurance_dates_get_the_same_flag():
    text = "Notice dated the date of the last signature. Payment is due three (3) years after the Effective Date."
    extracted = FallbackFields(parties=[], amounts=[], obligations=[], dates=[
        FallbackDate(value="the date of the last signature", label="notice date", source_quote="Notice dated the date of the last signature", page=1),
        FallbackDate(value="2023-01-01", label="due date", source_quote="due three (3) years after the Effective Date", page=1),
    ])
    got = by_name(validate_generic(extracted, [page(text)], TODAY))
    assert "date_not_concrete" in got["date_1.date"].flags and "date_not_concrete" in got["date_2.date"].flags
    coi = by_name(validate_coi(cert([policy(expiration_date=f("upon renewal", "01/15/2027"))]), pages_of("insurance_certificate.pdf"), TODAY))
    assert "date_not_concrete" in coi["policy_1.expiration_date"].flags
