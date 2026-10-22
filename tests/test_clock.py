import json
from datetime import date
from types import SimpleNamespace

import pytest

from paperbound import clock
from paperbound.agent import planner
from paperbound.pipeline import extract
from paperbound.pipeline.impact import build_brief
from paperbound.pipeline.types import DerivedObligation, ValidatedField
from paperbound.pipeline.validate import validate_fields
from tests.test_hardening import f, lease, make_page
from tests.test_planner import FakeClient, call, cal, email, response, lease_fields


def test_parameter_wins_over_env_and_system_date(monkeypatch):
    monkeypatch.setenv("AS_OF", "2026-10-22")
    assert clock.today(date(2030, 1, 2)) == date(2030, 1, 2)


def test_env_overrides_the_system_date(monkeypatch):
    monkeypatch.setenv("AS_OF", "2026-10-22")
    assert clock.today() == date(2026, 10, 22)


def test_system_date_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv("AS_OF", raising=False)
    assert clock.today() == date.today()
    monkeypatch.setenv("AS_OF", "  ")
    assert clock.today() == date.today()


@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-40", "10/22/2026"])
def test_bad_as_of_is_an_error_that_says_what_to_fix(monkeypatch, bad):
    monkeypatch.setenv("AS_OF", bad)
    with pytest.raises(ValueError, match="AS_OF must be YYYY-MM-DD"):
        clock.today()


# ---- every consumer of "today" follows the clock ----

def test_extraction_prompt_uses_the_clock(monkeypatch):
    monkeypatch.setenv("AS_OF", "2026-10-22")
    pages = [SimpleNamespace(page_no=1, text="alpha")]
    assert extract.build_user_message(pages).startswith("Today's date: 2026-10-22\n")
    assert extract.build_user_message(pages, date(2027, 2, 3)).startswith("Today's date: 2027-02-03\n")


TABLE = (
    "Year Period Monthly Base Rent 1 Apr 1, 2024 - Mar 31, 2025 $8,500.00 "
    "2 Apr 1, 2025 - Mar 31, 2026 $8,755.00 3 Apr 1, 2026 - Mar 31, 2027 $9,017.65"
)
ROW2 = "2 Apr 1, 2025 - Mar 31, 2026 $8,755.00"


def test_rent_table_row_follows_the_clock(monkeypatch):
    page = make_page(2, TABLE)
    claimed = lease(current_rent=f(8755.0, ROW2, 2))
    monkeypatch.setenv("AS_OF", "2026-10-22")  # year 3 is current, so year 2's rent conflicts
    assert "conflicting_values" in {x.name: x for x in validate_fields(claimed, [page])}["current_rent"].flags
    monkeypatch.setenv("AS_OF", "2025-08-01")  # year 2 is current
    assert "conflicting_values" not in {x.name: x for x in validate_fields(claimed, [page])}["current_rent"].flags


def vf(name, value):
    return ValidatedField(name, value, "q", 1, [0, 0, 1, 1], 1.0, 1.0, [], True)


def test_brief_days_remaining_follows_the_clock(monkeypatch):
    fields = [vf("end_date", "2027-03-31"), vf("notice_period_days", 90)]
    monkeypatch.setenv("AS_OF", "2026-10-22")
    assert build_brief(fields).value("days_remaining") == 70
    monkeypatch.setenv("AS_OF", "2026-12-21")
    assert build_brief(fields).value("days_remaining") == 10


def test_reminder_dates_follow_the_clock(monkeypatch):
    obligations = [DerivedObligation("deadline", date(2026, 12, 31), "d", ["end_date", "notice_period_days"])]
    fields = lease_fields()
    ids = {x.name: i for i, x in planner.plannable_fields(fields).items()}
    src = [ids["end_date"], ids["notice_period_days"]]

    def staged(as_of):
        monkeypatch.setenv("AS_OF", as_of)
        client = FakeClient(response(call("a", "create_calendar_event", **cal("2026-12-17", source_field_ids=src))), response(content="done"))
        planner.plan_actions(fields, obligations, client=client)  # no explicit today: the clock decides
        return json.loads(client.requests[0]["messages"][1]["content"])

    assert staged("2026-10-22")["reminder_dates"] == ["2026-11-01", "2026-12-01", "2026-12-17"]
    assert staged("2026-12-10")["reminder_dates"] == ["2026-12-17"]
    assert staged("2026-12-10")["today"] == "2026-12-10"
    assert staged("2027-01-05")["reminder_dates"] == []


def test_the_whole_pipeline_replays_as_of_another_day(monkeypatch):
    from tests.test_runner import PAGES, fake_classifier, fake_extractor, fake_planner
    from paperbound.pipeline.runner import make_parse_step, run_pipeline

    seen = {}

    def spy(fields, obligations, existing, **kwargs):
        seen["today"] = kwargs["today"]
        return fake_planner(fields, obligations, existing)

    monkeypatch.setenv("AS_OF", "2026-11-11")
    run_pipeline(PAGES, make_parse_step(PAGES, 1, False), extractor=fake_extractor, planner=spy, classifier=fake_classifier)
    assert seen["today"] == date(2026, 11, 11)
    # an explicit date beats the environment
    run_pipeline(PAGES, make_parse_step(PAGES, 1, False), extractor=fake_extractor, planner=spy,
                 classifier=fake_classifier, today=date(2026, 11, 12))
    assert seen["today"] == date(2026, 11, 12)
