from datetime import date

from paperbound.pipeline.impact import build_brief, fmt_date, fmt_money
from paperbound.pipeline.types import ValidatedField

TODAY = date(2026, 10, 22)


def vf(name, value, flags=None):
    return ValidatedField(name, value, "q", 1, [0, 0, 1, 1], 1.0, 1.0, flags or [], True)


def fields(**overrides):
    base = {
        "end_date": vf("end_date", "2027-03-31"),
        "notice_period_days": vf("notice_period_days", 90),
        "current_rent": vf("current_rent", 9017.65),
        "holdover_rate_percent": vf("holdover_rate_percent", 150.0),
        "rent_escalation_percent": vf("rent_escalation_percent", 3.0),
        "notice_method": vf("notice_method", "certified mail"),
        "notice_section": vf("notice_section", "Section 11"),
        "security_deposit": vf("security_deposit", 17000.0),
    }
    base.update(overrides)
    return [f for f in base.values() if f is not None]


def test_the_numbers_for_the_demo_lease():
    brief = build_brief(fields(), TODAY)
    assert brief.value("deadline") == "2026-12-31"
    assert brief.value("days_remaining") == 70  # Oct 22 -> Dec 31
    assert brief.value("current_rent") == 9017.65
    assert brief.value("next_rent") == 9288.18  # 9017.65 * 1.03 = 9288.1795
    assert brief.value("holdover_rent") == 13526.48  # 9017.65 * 1.5 = 13526.475, rounded half up
    assert brief.value("holdover_extra") == 4508.83
    assert brief.value("holdover_percent") == 150.0
    assert brief.value("notice_method") == "certified mail" and brief.value("notice_section") == "Section 11"
    assert brief.value("security_deposit") == 17000.0
    assert brief.skipped == []


def test_every_line_cites_its_source_fields():
    brief = build_brief(fields(), TODAY)
    cites = {ln.key: ln.source_fields for ln in brief.lines}
    assert cites["deadline"] == ["end_date", "notice_period_days"]
    assert cites["next_rent"] == ["current_rent", "rent_escalation_percent"]
    assert cites["holdover_rent"] == ["current_rent", "holdover_rate_percent"]
    assert cites["holdover_extra"] == ["current_rent", "holdover_rate_percent"]
    assert cites["notice_section"] == ["notice_section"]
    assert all(ln.source_fields for ln in brief.lines)


def test_display_text():
    brief = build_brief(fields(), TODAY)
    assert brief.line("deadline").text == "December 31, 2026"
    assert brief.line("days_remaining").text == "70 days left"
    assert brief.line("holdover_rent").text == "$13,526.48 / month"
    assert fmt_money(1234.5) == "$1,234.50" and fmt_date(date(2027, 3, 1)) == "March 1, 2027"


def test_overdue_deadline_is_reported_as_overdue():
    brief = build_brief(fields(), date(2027, 1, 5))
    assert brief.value("days_remaining") == -5
    assert brief.line("days_remaining").text == "5 days overdue"


def test_missing_current_rent_skips_every_line_that_needs_it():
    brief = build_brief(fields(current_rent=None), TODAY)
    skipped = {s["key"]: s["reason"] for s in brief.skipped}
    assert set(skipped) == {"current_rent", "next_rent", "holdover_rent", "holdover_extra", "holdover_percent"}
    assert "current_rent missing" in skipped["current_rent"] and "current_rent unusable" in skipped["holdover_rent"]
    assert brief.value("deadline") == "2026-12-31" and brief.value("notice_section") == "Section 11"


def test_missing_holdover_rate_only_skips_the_holdover_lines():
    brief = build_brief(fields(holdover_rate_percent=None), TODAY)
    assert {s["key"] for s in brief.skipped} == {"holdover_rent"}
    assert brief.value("current_rent") == 9017.65 and brief.value("next_rent") == 9288.18
    assert brief.line("holdover_extra") is None and brief.line("holdover_percent") is None


def test_missing_escalation_skips_only_next_rent():
    brief = build_brief(fields(rent_escalation_percent=None), TODAY)
    assert [s["key"] for s in brief.skipped] == ["next_rent"]
    assert brief.value("holdover_rent") == 13526.48


def test_blocked_fields_are_never_used():
    for flag in ("suspicious_instruction", "conflicting_values", "ungrounded", "value_not_in_quote", "quote_rule_failed"):
        brief = build_brief(fields(current_rent=vf("current_rent", 1.0, [flag])), TODAY)
        assert brief.line("current_rent") is None and brief.line("holdover_rent") is None, flag
        assert any(flag in s["reason"] for s in brief.skipped if s["key"] == "current_rent"), flag
        assert "$1.00" not in "".join(ln.text for ln in brief.lines)


def test_no_deadline_when_notice_period_is_unusable():
    brief = build_brief(fields(notice_period_days=vf("notice_period_days", 90, ["invalid_notice_period"])), TODAY)
    assert brief.due_date is None and brief.line("deadline") is None and brief.line("days_remaining") is None
    assert {"deadline", "days_remaining"} <= {s["key"] for s in brief.skipped}
    assert brief.value("holdover_rent") == 13526.48  # rent lines do not depend on the deadline


def test_rounding_is_half_up_to_cents():
    brief = build_brief(fields(current_rent=vf("current_rent", 100.05)), TODAY)
    assert brief.value("holdover_rent") == 150.08  # 150.075 -> .08
    assert brief.value("holdover_extra") == 50.03


def test_empty_input_gives_only_skips():
    brief = build_brief([], TODAY)
    assert brief.lines == [] and len(brief.skipped) >= 8
