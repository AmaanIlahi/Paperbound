from paperbound.agent.policy import decide
from paperbound.agent.tools import TOOLS
from paperbound.pipeline.types import ValidatedField


def vf(conf):
    return ValidatedField("end_date", "2027-03-31", "q", 1, [0, 0, 1, 1], conf, 1.0, [], True)


def test_read_only_tools_are_auto():
    for name in ("get_obligations", "get_entity"):
        assert decide(TOOLS[name], []).status == "auto"


def test_side_effect_actions_are_pending_by_default():
    for name in ("create_calendar_event", "upsert_record", "fire_webhook", "draft_email"):
        assert decide(TOOLS[name], [vf(1.0)]).status == "pending"


def test_draft_email_is_always_a_draft_even_with_auto_approval_on():
    decision = decide(TOOLS["draft_email"], [vf(1.0)], auto_approve_reversible=True)
    assert decision.status == "pending"
    assert "draft" in decision.reason.lower()


def test_auto_approval_needs_reversible_and_high_confidence():
    calendar = TOOLS["create_calendar_event"]
    assert decide(calendar, [vf(0.95), vf(0.9)], auto_approve_reversible=True).status == "approved"
    assert decide(calendar, [vf(0.95), vf(0.89)], auto_approve_reversible=True).status == "pending"
    assert decide(calendar, [], auto_approve_reversible=True).status == "pending"
    assert decide(TOOLS["fire_webhook"], [vf(1.0)], auto_approve_reversible=True).status == "pending"


def test_tool_flags_match_the_architecture():
    assert not TOOLS["get_obligations"].side_effect and not TOOLS["get_entity"].side_effect
    assert TOOLS["create_calendar_event"].side_effect and TOOLS["create_calendar_event"].reversible
    assert TOOLS["draft_email"].side_effect and TOOLS["draft_email"].always_draft
    assert TOOLS["fire_webhook"].side_effect and not TOOLS["fire_webhook"].reversible
