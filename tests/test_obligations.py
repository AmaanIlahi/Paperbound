from datetime import date

from paperbound.pipeline.obligations import derive_obligations
from paperbound.pipeline.types import ValidatedField


def vf(name, value, flags=None):
    return ValidatedField(name, value, "q", 1, [0, 0, 1, 1], 1.0, 1.0, flags or [], True)


def test_notice_deadline_for_sample_lease():
    obligations, skipped = derive_obligations([vf("end_date", "2027-03-31"), vf("notice_period_days", 90)])
    assert skipped == []
    assert len(obligations) == 1
    o = obligations[0]
    assert o.due_date == date(2026, 12, 31)
    assert o.kind == "deadline"
    assert o.source_fields == ["end_date", "notice_period_days"]
    assert "2026-12-31" in o.description


def test_deadline_crosses_leap_day():
    obligations, _ = derive_obligations([vf("end_date", "2028-03-01"), vf("notice_period_days", 30)])
    assert obligations[0].due_date == date(2028, 1, 31)
    obligations, _ = derive_obligations([vf("end_date", "2028-03-01"), vf("notice_period_days", 1)])
    assert obligations[0].due_date == date(2028, 2, 29)


def test_missing_end_date_is_skipped():
    obligations, skipped = derive_obligations([vf("end_date", None, ["not_found"]), vf("notice_period_days", 90)])
    assert obligations == []
    assert "end_date missing" in skipped[0]


def test_missing_notice_is_skipped():
    obligations, skipped = derive_obligations([vf("end_date", "2027-03-31")])
    assert obligations == []
    assert "notice_period_days missing" in skipped[0]


def test_ungrounded_input_is_skipped():
    obligations, skipped = derive_obligations(
        [vf("end_date", "2027-03-31"), vf("notice_period_days", 90, ["ungrounded"])]
    )
    assert obligations == []
    assert "ungrounded" in skipped[0]


def test_invalid_input_is_skipped():
    obligations, skipped = derive_obligations(
        [vf("end_date", "2027-03-31", ["end_before_start"]), vf("notice_period_days", 90)]
    )
    assert obligations == []
    assert "failed validation" in skipped[0]
