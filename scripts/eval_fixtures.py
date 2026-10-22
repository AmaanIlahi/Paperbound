"""Run the full pipeline on every fixtures/demo/*.expected.json and compare with what the code produced.

Usage:
  python scripts/eval_fixtures.py                      # every fixture (about 2 OpenAI calls each, none for the classifier)
  python scripts/eval_fixtures.py --only lease         # fixtures whose pdf or expected-file name contains "lease"
  python scripts/eval_fixtures.py --as-of 2026-11-20   # replay as of another day

Each expected file names its doc_type, its pdfs and (optionally) as_of. The date for a run is --as-of, else the file's
as_of, else the AS_OF environment variable, else 2026-10-22, so results do not drift with the calendar. Expected values
that depend on the date (current rent, days remaining, which reminders are still in the future) are recomputed here
from the expected data, independently of the code under test. Does not touch the database.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperbound import config  # noqa: E402
from paperbound.pipeline.parse import parse_pdf  # noqa: E402
from paperbound.pipeline.runner import make_parse_step, run_pipeline  # noqa: E402

DEFAULT_AS_OF = "2026-10-22"
DEMO = ROOT / "fixtures" / "demo"
EXPIRING_WINDOW_DAYS = 60


def cents(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def matches(name: str, want, got) -> bool:
    if got is None:
        return want is None
    if want is None:
        return False
    if isinstance(want, dict):
        return all(word in str(got).casefold() for word in want["contains"])
    base = name.rsplit(".", 1)[-1]
    if base in ("rent_amount", "security_deposit", "current_rent", "holdover_rate_percent", "rent_escalation_percent",
                "each_occurrence_limit", "aggregate_limit"):
        return abs(float(got) - float(want)) < 0.005
    if base in ("premises_address", "insured", "certificate_holder", "producer", "insurer"):
        return fuzz.token_set_ratio(str(want), str(got)) >= 85
    if isinstance(want, str):
        return str(got).strip().rstrip(".").casefold() == want.strip().rstrip(".").casefold()
    return got == want


def field_row(name, want, f, label=None):
    got = f.value if f else None
    return {
        "field": label or name, "expected": want, "extracted": got, "match": matches(name, want, got),
        "confidence": f.confidence if f else None,
        "grounded": bool(f and f.value is not None and "ungrounded" not in f.flags),
        "flags": f.flags if f else [], "page": f.page_no if f else None, "quote": f.source_quote if f else None,
    }


# ---------------------------------------------------------------------------------------------------------------
# lease
# ---------------------------------------------------------------------------------------------------------------

def rent_today(expected: dict, today: date) -> float | None:
    for row in expected["rent_schedule"]:
        if date.fromisoformat(row["start"]) <= today <= date.fromisoformat(row["end"]):
            return row["amount"]
    return None


def lease_rows(expected: dict, fields: list, today: date) -> list[dict]:
    by_name = {f.name: f for f in fields}
    want = {
        "landlord": expected["parties"]["landlord"], "tenant": expected["parties"]["tenant"],
        "premises_address": expected["premises"], "start_date": expected["start_date"], "end_date": expected["end_date"],
        "rent_amount": expected["rent_amount"], "rent_period": expected["rent_period"],
        "notice_period_days": expected["notice_period_days"], "holdover_rate_percent": expected["holdover_rate_percent"],
        "security_deposit": expected["security_deposit"], "notice_method": {"contains": expected["notice_method_contains"]},
        "notice_section": expected["notice_section"], "rent_escalation_percent": expected["rent_escalation_percent"],
        "current_rent": rent_today(expected, today),
    }
    return [field_row(n, w, by_name.get(n)) for n, w in want.items()]


def lease_obligation(expected: dict, result) -> dict:
    want = expected["obligations"][0]
    got = next((o for o in result.obligations if o.kind == want["kind"]), None)
    return {"expected": want["due_date"], "got": got.due_date.isoformat() if got else None,
            "match": bool(got and got.due_date.isoformat() == want["due_date"])}


def lease_brief(expected: dict, brief, today: date) -> dict:
    due = date.fromisoformat(expected["obligations"][0]["due_date"])
    current = rent_today(expected, today)
    want = {"deadline": due.isoformat(), "days_remaining": (due - today).days, "current_rent": current}
    if current is not None:
        holdover = cents(Decimal(str(current)) * Decimal(str(expected["holdover_rate_percent"])) / 100)
        want |= {
            "next_rent": cents(Decimal(str(current)) * (1 + Decimal(str(expected["rent_escalation_percent"])) / 100)),
            "holdover_rent": holdover,
            "holdover_extra": cents(Decimal(str(holdover)) - Decimal(str(current))),
        }
    return brief_result(brief, want)


def brief_result(brief, want: dict) -> dict:
    got = {k: (brief.value(k) if brief else None) for k in want}
    rows = [{"line": k, "expected": want[k], "got": got[k], "match": got[k] == want[k]} for k in want]
    cited = bool(brief) and bool(brief.lines) and all(ln.source_fields for ln in brief.lines)
    return {"lines": rows, "all_cite_sources": cited, "skipped": brief.skipped if brief else [],
            "match": all(r["match"] for r in rows) and cited}


def reminder_dates(expected: dict, due: date, today: date) -> list[date]:
    return [due - timedelta(days=d) for d in expected["reminder_lead_days"] if due - timedelta(days=d) > today]


def lease_actions(expected: dict, plan, today: date) -> dict:
    actions = plan.actions if plan else []
    due = date.fromisoformat(expected["obligations"][0]["due_date"])
    current = rent_today(expected, today)
    checks = []
    got = {a.args.get("date"): a for a in actions if a.tool == "create_calendar_event"}
    for when in reminder_dates(expected, due, today):
        a = got.get(when.isoformat())
        text = a.args.get("description", "") if a else ""
        ok = bool(a) and "Clause (page" in text and "Source: /#run=" in text and ("holdover rent of" in text if current is not None else True)
        checks.append({"expected": f"reminder on {when.isoformat()} with clause, consequence and source link", "match": ok})
    for variant, stems in expected["email_variants"].items():
        found = None
        for a in actions:
            if a.tool != "draft_email" or a.group_key != "notice_choice":
                continue
            text = f"{a.args.get('subject', '')} {a.args.get('body', '')}"
            if (any(s in text.casefold() for s in stems) and expected["parties"]["landlord"] in text
                    and expected["parties"]["tenant"] in text and expected["notice_section"] in text
                    and (current is None or f"${current:,.2f}" in text) and a.args.get("to") == "{{RECIPIENT}}" and "{" not in text):
                found = a
                break
        checks.append({"expected": f"{variant} email citing names, {expected['notice_section']}, deadline, current rent", "match": found is not None})
    record = next((a for a in actions if a.tool == "upsert_record"), None)
    data = record.args["data"] if record else {}
    checks.append({"expected": "tracker record: deadline, counterparty, status 'decision needed'",
                   "match": bool(record) and data.get("status") == expected["tracker_status"] and data.get("deadline") == due.isoformat()
                   and data.get("counterparty") == expected["parties"]["landlord"]})
    drafts = [a for a in actions if a.tool == "draft_email"]
    checks.append({"expected": "the two drafts are one exclusive choice", "match": len(drafts) == 2 and {a.group_mode for a in drafts} == {"exclusive"}})
    return actions_result(checks, plan)


# ---------------------------------------------------------------------------------------------------------------
# insurance certificate
# ---------------------------------------------------------------------------------------------------------------

def policy_index(fields: list, number: str) -> int | None:
    for f in fields:
        if f.name.endswith(".number") and f.value is not None and str(f.value).strip().casefold() == number.casefold():
            return int(f.name.split(".")[0].split("_")[1])
    return None


def coi_rows(expected: dict, fields: list) -> list[dict]:
    by_name = {f.name: f for f in fields}
    rows = [field_row(n, expected[n], by_name.get(n)) for n in ("insured", "certificate_holder", "producer")]
    extracted_count = len({f.name.split(".")[0] for f in fields if f.name.startswith("policy_")})
    rows.append({"field": "policies (count)", "expected": len(expected["policies"]), "extracted": extracted_count,
                 "match": extracted_count == len(expected["policies"]), "confidence": None, "grounded": True, "flags": [], "page": None, "quote": None})
    for p in expected["policies"]:
        k = policy_index(fields, p["number"])
        for name in ("type", "insurer", "number", "effective_date", "expiration_date", "each_occurrence_limit", "aggregate_limit"):
            want = {"contains": [p["type_contains"]]} if name == "type" else p[name]
            f = by_name.get(f"policy_{k}.{name}") if k else None
            rows.append(field_row(name, want, f, label=f"{p['number']}.{name}"))
    return rows


def coi_obligation(expected: dict, result) -> dict:
    want = sorted(p["expiration_date"] for p in expected["policies"])
    got = sorted(o.due_date.isoformat() for o in result.obligations if o.kind == "expiration")
    return {"expected": want, "got": got, "match": want == got}


def coi_brief(expected: dict, brief, fields: list, today: date) -> dict:
    exps = {p["number"]: date.fromisoformat(p["expiration_date"]) for p in expected["policies"]}
    earliest = min(exps.values())
    want = {"earliest_expiration": earliest.isoformat(), "days_remaining": (earliest - today).days,
            "expiring_soon": sum(1 for d in exps.values() if (d - today).days <= EXPIRING_WINDOW_DAYS)}
    minimum = config.coi_min_each_occurrence()
    for p in expected["policies"]:
        k = policy_index(fields, p["number"])
        if k:
            want[f"limit_check_{k}"] = p["each_occurrence_limit"] >= minimum
    return brief_result(brief, want)


def coi_actions(expected: dict, plan, today: date) -> dict:
    actions = plan.actions if plan else []
    earliest = min(date.fromisoformat(p["expiration_date"]) for p in expected["policies"])
    when_text = f"{earliest:%B} {earliest.day}, {earliest.year}"
    checks = []
    got = {a.args.get("date"): a for a in actions if a.tool == "create_calendar_event"}
    for when in reminder_dates(expected, earliest, today):
        a = got.get(when.isoformat())
        text = a.args.get("description", "") if a else ""
        ok = bool(a) and all(p["number"] in text for p in expected["policies"]) and "Source: /#run=" in text and "minimum" in text
        checks.append({"expected": f"reminder on {when.isoformat()} listing every policy, its limit check and a source link", "match": ok})
    mails = [a for a in actions if a.tool == "draft_email"]
    mail_ok = len(mails) == 1 and all(
        x in f"{mails[0].args.get('subject', '')} {mails[0].args.get('body', '')}"
        for x in (expected["insured"], expected["certificate_holder"], when_text)
    ) and mails[0].args.get("to") == "{{RECIPIENT}}" and "{" not in mails[0].args.get("body", "") and "renew" in mails[0].args.get("body", "").casefold()
    checks.append({"expected": "one email, holder to insured, asking for a renewed certificate before the earliest expiration", "match": mail_ok})
    record = next((a for a in actions if a.tool == "upsert_record"), None)
    data = record.args["data"] if record else {}
    checks.append({"expected": "tracker record: deadline, insured, status 'renewal needed'",
                   "match": bool(record) and data.get("status") == expected["tracker_status"] and data.get("deadline") == earliest.isoformat()
                   and data.get("insured") == expected["insured"]})
    return actions_result(checks, plan)


def actions_result(checks: list[dict], plan) -> dict:
    actions = plan.actions if plan else []
    return {
        "checks": checks, "match": bool(checks) and all(c["match"] for c in checks),
        "extra": [a.tool for a in actions if a.tool not in {"create_calendar_event", "draft_email", "upsert_record"}],
        "proposed": [{"tool": a.tool, "args": a.args, "source_fields": a.source_fields,
                      "group": f"{a.group_key}/{a.group_mode}" if a.group_key else None,
                      "policy": f"{a.policy_status}: {a.policy_reason}"} for a in actions],
        "rejected": plan.rejected if plan else [], "notes": plan.notes if plan else [],
    }


# ---------------------------------------------------------------------------------------------------------------
# running and reporting
# ---------------------------------------------------------------------------------------------------------------

def run_one(pdf: Path, expected: dict, as_of: date) -> dict:
    os.environ["AS_OF"] = as_of.isoformat()  # the whole run, including the extraction prompt, sees this date
    started = time.perf_counter()
    parse_started = time.perf_counter()
    pages = parse_pdf(pdf.read_bytes())
    parse_ms = int((time.perf_counter() - parse_started) * 1000)
    result = run_pipeline(pages, make_parse_step(pages, parse_ms, reused=False), today=as_of)
    latency_ms = int((time.perf_counter() - started) * 1000)

    doc_type = expected["doc_type"]
    if result.status != "done":
        print(f"  PIPELINE {result.status.upper()}: {[f'{s.stage}: {s.error}' for s in result.steps if s.error]}")
    classification = {
        "expected": doc_type, "got": result.doc_type, "method": result.classification.method if result.classification else None,
        "reason": result.classification.reason if result.classification else None,
        "match": result.doc_type == doc_type,
    }
    if doc_type == "lease":
        rows = lease_rows(expected, result.fields, as_of)
        obligation, brief = lease_obligation(expected, result), lease_brief(expected, result.brief, as_of)
        actions = lease_actions(expected, result.plan, as_of)
    else:
        rows = coi_rows(expected, result.fields)
        obligation, brief = coi_obligation(expected, result), coi_brief(expected, result.brief, result.fields, as_of)
        actions = coi_actions(expected, result.plan, as_of)

    calls = (1 if result.classification and result.classification.method == "llm" else 0) + 1 + (result.plan.api_calls if result.plan else 0)
    extra = {}
    if doc_type == "lease":
        rent = next((f for f in result.fields if f.name == "rent_amount"), None)
        extra = {"rent_amount_extracted": rent.value if rent else None, "rent_amount_flags": rent.flags if rent else []}
    summary = {
        "pdf": pdf.name, "doc_type": doc_type, "as_of": as_of.isoformat(), "run_status": result.status,
        "classified": f"{result.doc_type} ({classification['method']})", "classification_match": classification["match"],
        "fields_matched": f"{sum(r['match'] for r in rows)}/{len(rows)}", "obligation_match": obligation["match"],
        "brief_match": brief["match"], "brief_lines": f"{sum(r['match'] for r in brief['lines'])}/{len(brief['lines'])}",
        "actions_match": actions["match"], "action_checks": f"{sum(c['match'] for c in actions['checks'])}/{len(actions['checks'])}",
        "actions_proposed": len(actions["proposed"]), **extra,
        "openai_calls": calls, "tokens": result.tokens, "cost_usd": result.cost_usd, "latency_ms": latency_ms,
    }
    return {"summary": summary, "classification": classification, "fields": rows, "obligation": obligation, "brief": brief,
            "actions": actions, "steps": [vars(s) for s in result.steps]}


def print_detail(out: dict) -> None:
    s = out["summary"]
    print(f"\n=== {s['pdf']}  ({s['doc_type']}, as of {s['as_of']})")
    c = out["classification"]
    print(f"classified as {c['got']} via {c['method']} -> {'yes' if c['match'] else 'NO'}")
    print(f"{'field':34} {'expected':28} {'extracted':28} match  conf  grounded flags")
    for r in out["fields"]:
        want = r["expected"]["contains"] if isinstance(r["expected"], dict) else r["expected"]
        conf = "-" if r["confidence"] is None else format(r["confidence"], ".2f")
        print(f"{r['field']:34} {str(want)[:27]:28} {str(r['extracted'])[:27]:28} {'yes' if r['match'] else 'NO':5}  {conf:>4}  "
              f"{'yes' if r['grounded'] else 'no':8} {','.join(r['flags'])}")
    o = out["obligation"]
    print(f"\nobligations: expected {o['expected']}, got {o['got']} -> {'yes' if o['match'] else 'NO'}")
    print("decision brief (code vs independent recomputation):")
    for b in out["brief"]["lines"]:
        print(f"  {b['line']:20} expected {str(b['expected']):12} got {str(b['got']):12} -> {'yes' if b['match'] else 'NO'}")
    print(f"  every line cites source fields -> {'yes' if out['brief']['all_cite_sources'] else 'NO'}")
    if out["brief"]["skipped"]:
        print(f"  skipped: {out['brief']['skipped']}")
    print("proposed actions:")
    for a in out["actions"]["proposed"]:
        detail = a["args"].get("date") or a["args"].get("subject") or a["args"].get("key") or ""
        print(f"  {a['tool']:22} {str(detail)[:44]:44} {str(a['group'] or ''):24} [{a['policy'].split(':')[0]}]")
    for ck in out["actions"]["checks"]:
        print(f"  check: {ck['expected'][:92]:92} -> {'yes' if ck['match'] else 'NO'}")
    if out["actions"]["extra"] or out["actions"]["rejected"]:
        print(f"  extra actions: {out['actions']['extra']}  rejected proposals: {out['actions']['rejected']}")
    if out["actions"]["notes"]:
        print(f"  planner notes: {out['actions']['notes']}")
    print(f"tokens {s['tokens']}  cost ${s['cost_usd']:.5f}  latency {s['latency_ms']} ms  openai calls {s['openai_calls']}")


def print_tables(outs: list[dict]) -> None:
    for doc_type in sorted({o["summary"]["doc_type"] for o in outs}):
        rows = [o["summary"] for o in outs if o["summary"]["doc_type"] == doc_type]
        print(f"\n## {doc_type}")
        print(f"{'fixture':26} {'classified':26} {'fields':7} {'oblig':6} {'brief':6} {'actions':8} {'calls':5} {'cost':9} {'latency':8}")
        for s in rows:
            print(f"{s['pdf']:26} {s['classified'][:25]:26} {s['fields_matched']:7} {'yes' if s['obligation_match'] else 'NO':6} "
                  f"{s['brief_lines']:6} {s['action_checks']:8} {s['openai_calls']:<5} ${s['cost_usd']:<8.5f} {s['latency_ms'] / 1000:>6.1f} s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run only fixtures whose pdf or expected-file name contains this text")
    parser.add_argument("--as-of", help="YYYY-MM-DD; overrides the date in the expected files")
    args = parser.parse_args()

    original_as_of = os.environ.get("AS_OF")
    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    outs = []
    for expected_path in sorted(DEMO.glob("*.expected.json")):
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        for pdf_name in expected["pdfs"]:
            if args.only and args.only not in pdf_name and args.only not in expected_path.name:
                continue
            as_of = date.fromisoformat(args.as_of or expected.get("as_of") or original_as_of or DEFAULT_AS_OF)
            out = run_one(DEMO / pdf_name, expected, as_of)
            print_detail(out)
            path = out_dir / f"{Path(pdf_name).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
            print(f"saved {path.relative_to(ROOT)}")
            outs.append(out)
    if not outs:
        sys.exit("no fixture matched")
    print_tables(outs)
    print(f"\nOpenAI calls: {sum(o['summary']['openai_calls'] for o in outs)}   cost ${sum(o['summary']['cost_usd'] for o in outs):.5f}")


if __name__ == "__main__":
    main()
