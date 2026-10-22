"""A fake extractor and planner for the lease fixture: quotes that ground in the real text, canned model replies."""

from datetime import date
from pathlib import Path

from paperbound.agent.planner import plan_actions
from paperbound.pipeline.extract import ExtractionResult
from paperbound.schemas.lease import LeaseFields
from tests.test_planner import FakeClient, call, cal, email, response

LEASE = Path(__file__).resolve().parent.parent / "fixtures" / "demo" / "lease.pdf"
TODAY = date(2026, 10, 22)

QUOTES = {
    "landlord": ("Harborview Properties LLC", "Harborview Properties LLC", 1),
    "tenant": ("Brightline Studio Inc.", "Brightline Studio Inc.", 1),
    "premises_address": ("220 Hudson Street, New York, NY 10013", "located at 220 Hudson Street, New York, NY 10013", 1),
    "start_date": ("2024-04-01", "commencing on April 1, 2024", 2),
    "end_date": ("2027-03-31", "expiring at 11:59 p.m. on March 31, 2027", 2),
    "rent_amount": (8500.0, "initial rate of Eight Thousand Five Hundred Dollars ($8,500.00) per month", 2),
    "rent_period": ("monthly", "($8,500.00) per month", 2),
    "notice_period_days": (90, "at least ninety (90) days before the Expiration Date", 3),
    "holdover_rate_percent": (150.0, "one hundred fifty percent (150%) of the Base Rent in effect on the Expiration Date", 3),
    "security_deposit": (17000.0, "deposit with Landlord the sum of $17,000.00 as security", 2),
    "notice_method": ("certified mail or nationally recognized overnight courier",
                      "Notice shall be delivered by certified mail or nationally recognized overnight courier", 3),
    "notice_section": ("Section 11", "11. Renewal; Termination at Expiration", 3),
    "rent_escalation_percent": (3.0, "Base Rent shall increase by three percent (3%)", 2),
    "current_rent": (9017.65, "3 Apr 1, 2026 - Mar 31, 2027 $9,017.65", 2),
}


def fake_extractor(pages):
    data = {n: {"value": v, "source_quote": q, "page": p} for n, (v, q, p) in QUOTES.items()}
    return ExtractionResult(LeaseFields.model_validate(data), "fake-model", 10, 0.0)


def fake_planner(fields, obligations, existing, **kwargs):
    ids = [1, 2]
    reminders = [
        call(f"c{i}", "create_calendar_event", **cal(d, source_field_ids=ids))
        for i, d in enumerate(("2026-11-01", "2026-12-01", "2026-12-17"))
    ]
    client = FakeClient(
        response(
            *reminders,
            call("e1", "draft_email", **email("renew", source_field_ids=ids)),
            call("e2", "draft_email", **email("terminate", source_field_ids=ids)),
        )
    )
    return plan_actions(fields, obligations, existing, client=client, **kwargs)
