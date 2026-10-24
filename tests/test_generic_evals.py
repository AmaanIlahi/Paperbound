"""The generic eval: fixtures are well formed, scoring is by value, and the report treats the suite separately."""

import json
from datetime import date

from paperbound.evals import generic as generic_eval
from paperbound.evals import report
from paperbound.pipeline.classify import Classification
from paperbound.pipeline.parse import parse_pdf
from paperbound.pipeline.runner import make_parse_step, run_pipeline
from paperbound.schemas.generic import FallbackAmount, FallbackDate, FallbackFields, FallbackObligation, FallbackParty
from tests.generic_fakes import fake_generic_extractor

AS_OF = date(2026, 10, 22)
FIRST = generic_eval.FIXTURES / "generic_001"


def test_there_are_five_generic_fixtures_and_one_is_a_capital_call_notice():
    folders = generic_eval.discover()
    assert [f.name for f in folders] == [f"generic_00{i}" for i in range(1, 6)]
    titles = [parse_pdf((f / "doc.pdf").read_bytes())[0].text for f in folders]
    assert any("CAPITAL CALL NOTICE" in t for t in titles)
    for folder in folders:
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        assert expected["doc_type"] == "generic" and expected["parties"] and expected["dates"] and expected["amounts"]
        assert set(expected["deadlines"]) <= {d["value"] for d in expected["dates"]}
        assert expected["actions"]["tools_allowed"] == ["create_calendar_event", "flag_for_review"]
        # the ground truth only expects reminders for dates still ahead of the eval's AS_OF
        assert all(date.fromisoformat(d) > AS_OF for d in expected["actions"]["reminder_dates"])


def right_answer() -> FallbackFields:
    q = lambda text: text  # noqa: E731  (the quotes below are printed in generic_001)
    return FallbackFields(
        parties=[FallbackParty(name="Northgate Growth Fund II, L.P.", role="issuer", source_quote=q("Northgate Growth Fund II, L.P."), page=1),
                 FallbackParty(name="Juniper Family Office LLC", role="investor", source_quote=q("Juniper Family Office LLC"), page=1)],
        dates=[FallbackDate(value="2026-10-01", label="notice date", source_quote="Notice date: October 1, 2026", page=1),
               FallbackDate(value="2026-11-15", label="payment due date", source_quote="to the Fund account by November 15, 2026", page=1),
               FallbackDate(value="2026-11-08", label="acknowledgement due", source_quote="of this notice by November 8, 2026", page=1),
               FallbackDate(value="2026-06-30", label="previous call settled", source_quote="was settled on June 30, 2026", page=1),
               FallbackDate(value="2026-07-04", label="made up", source_quote="was settled on June 30, 2026", page=1)],  # an extra
        amounts=[FallbackAmount(value=250000.0, currency="USD", label="capital call amount", source_quote="Capital call amount: $250,000.00 USD", page=1),
                 FallbackAmount(value=2000000.0, currency="USD", label="total commitment", source_quote="total commitment is $2,000,000.00", page=1),
                 FallbackAmount(value=1250000.0, currency="USD", label="unfunded", source_quote="of which $1,250,000.00 remains unfunded", page=1)],
        obligations=[
            FallbackObligation(description="Wire the capital call amount", due_date="2026-11-15",
                               source_quote="Please wire the capital call amount to the Fund account by November 15, 2026.", page=1),
            FallbackObligation(description="Return the signed acknowledgement", due_date="2026-11-08",
                               source_quote="Please return the signed acknowledgement of this notice by November 8, 2026.", page=1)],
    )


def record(extracted: FallbackFields, label="capital call notice") -> dict:
    pages = parse_pdf((FIRST / "doc.pdf").read_bytes())
    cls = Classification("generic", "llm", "x", type_label=label)
    result = run_pipeline(pages, make_parse_step(pages, 1, False), extractor=lambda p: fake_generic_extractor(p, extracted),
                          classifier=lambda p: cls, today=AS_OF)
    expected = json.loads((FIRST / "expected.json").read_text(encoding="utf-8"))
    rows, extras = generic_eval.score_fields(expected, result)
    return {
        "id": "generic_001", "doc_type": "generic", "variant": "clean", "template": "generic", "status": result.status, "errors": [],
        "classification": {"expected": "generic", "got": result.doc_type, "method": "llm", "match": True, "type_label": label,
                           "label_ok": generic_eval._has(label, expected["type_label_any"])},
        "fields": rows, "extras": extras, "obligation": generic_eval.score_obligations(expected, result),
        "actions": generic_eval.score_actions(expected, result, 0.7), "cost_usd": 0.001, "tokens": 1000, "latency_ms": 900, "calls": 2,
    }


def test_a_correct_extraction_scores_fully_and_an_extra_field_costs_precision():
    rec = record(right_answer())
    assert all(r["match"] for r in rec["fields"]) and len(rec["fields"]) == 11
    assert [e["value"] for e in rec["extras"]] == ["2026-07-04"]
    assert rec["obligation"]["match"] and rec["actions"]["match"] and rec["actions"]["cap_ok"] and rec["actions"]["no_email"]
    s = generic_eval.summarize([rec])
    assert s["field_accuracy"] == 1.0 and abs(s["field_precision"] - 11 / 12) < 1e-9
    assert s["party_accuracy"] == s["date_accuracy"] == s["amount_accuracy"] == s["obligation_accuracy"] == 1.0
    assert s["confidence_cap_respected"] == 1.0 and s["email_drafts"] == 0


def test_a_wrong_value_a_wrong_currency_and_a_miss_are_each_counted():
    wrong = right_answer()
    wrong.amounts[0] = FallbackAmount(value=250000.0, currency="EUR", label="capital call amount", source_quote="Capital call amount: $250,000.00 USD", page=1)
    wrong.parties.pop()
    rec = record(wrong)
    by_key = {(r["category"], r["key"]): r for r in rec["fields"]}
    assert not by_key[("amount", "250000.0")]["match"] and not by_key[("party", "Juniper Family Office LLC")]["match"]
    s = generic_eval.summarize([rec])
    assert s["amount_accuracy"] < 1.0 and s["party_accuracy"] == 0.5
    kinds = {item["kind"] for item in generic_eval.failures([rec])}
    assert {"wrong_field", "missed_field", "extra_field"} <= kinds


def test_the_generic_suite_has_its_own_metrics_and_baseline_keys():
    s = generic_eval.summarize([record(right_answer())])
    flat = report.flat_metrics("generic", {"generic/all": s})
    assert flat["generic/generic/all/field_accuracy"] == 1.0 and "generic/generic/all/confidence_cap_respected" in flat
    assert not any(k.startswith("synthetic/") for k in flat)
