"""Run the full pipeline on a lease PDF and compare against its expected JSON.

Usage: python scripts/eval_lease.py [--pdf fixtures/demo/lease.pdf] [--expected fixtures/demo/lease.expected.json]
Makes about two OpenAI calls (extraction + planner). Does not touch the database.
Expected values that depend on today's date (current rent, which reminders are still in the future) are computed
here from the expected rent schedule and the deadline, independently of the code under test.
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperbound.pipeline.parse import parse_pdf  # noqa: E402
from paperbound.pipeline.runner import make_parse_step, run_pipeline  # noqa: E402


def cents(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def rent_today(expected: dict, today: date) -> float | None:
    for row in expected["rent_schedule"]:
        if date.fromisoformat(row["start"]) <= today <= date.fromisoformat(row["end"]):
            return row["amount"]
    return None


def expected_values(expected: dict, today: date) -> dict:
    return {
        "landlord": expected["parties"]["landlord"],
        "tenant": expected["parties"]["tenant"],
        "premises_address": expected["premises"],
        "start_date": expected["start_date"],
        "end_date": expected["end_date"],
        "rent_amount": expected["rent_amount"],
        "rent_period": expected["rent_period"],
        "notice_period_days": expected["notice_period_days"],
        "holdover_rate_percent": expected["holdover_rate_percent"],
        "security_deposit": expected["security_deposit"],
        "notice_method": {"contains": expected["notice_method_contains"]},
        "notice_section": expected["notice_section"],
        "rent_escalation_percent": expected["rent_escalation_percent"],
        "current_rent": rent_today(expected, today),
    }


def matches(name: str, want, got) -> bool:
    if got is None:
        return want is None
    if want is None:
        return False
    if isinstance(want, dict):
        return all(word in str(got).casefold() for word in want["contains"])
    if name in ("rent_amount", "security_deposit", "current_rent", "holdover_rate_percent", "rent_escalation_percent"):
        return abs(float(got) - float(want)) < 0.005
    if name == "premises_address":
        return fuzz.token_set_ratio(str(want), str(got)) >= 85
    if name == "notice_section":
        return str(got).strip().rstrip(".").casefold() == str(want).strip().casefold()
    if isinstance(want, str):
        return str(got).strip().rstrip(".").casefold() == want.strip().rstrip(".").casefold()
    return got == want


def check_brief(expected: dict, brief, today: date) -> dict:
    """Recompute the brief numbers from the expected values and compare with what the code produced."""
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
    got = {k: (brief.value(k) if brief else None) for k in want}
    rows = [{"line": k, "expected": want[k], "got": got[k], "match": got[k] == want[k]} for k in want]
    cited = bool(brief) and all(ln.source_fields for ln in brief.lines)
    return {"lines": rows, "all_cite_sources": cited, "skipped": brief.skipped if brief else [],
            "match": all(r["match"] for r in rows) and cited}


def check_actions(expected: dict, plan, today: date, current: float | None) -> dict:
    """Staged reminders, renew/terminate drafts, tracker record. Extra actions are reported, not penalized."""
    actions = plan.actions if plan else []
    due = date.fromisoformat(expected["obligations"][0]["due_date"])
    checks = []

    want_dates = [due - timedelta(days=d) for d in expected["reminder_lead_days"] if due - timedelta(days=d) > today]
    got = {a.args.get("date"): a for a in actions if a.tool == "create_calendar_event"}
    for when in want_dates:
        a = got.get(when.isoformat())
        text = a.args.get("description", "") if a else ""
        ok = bool(a) and "Clause (page" in text and "Source: /#run=" in text and (
            "holdover rent of" in text if current is not None else True
        )
        checks.append({"expected": f"reminder on {when.isoformat()} with clause, consequence and source link",
                       "tool": "create_calendar_event", "match": ok})

    for variant, stems in expected["email_variants"].items():
        found = None
        for a in actions:
            if a.tool != "draft_email" or a.group_key != "notice_choice":
                continue
            text = f"{a.args.get('subject', '')} {a.args.get('body', '')}"
            if (
                any(s in text.casefold() for s in stems)
                and expected["parties"]["landlord"] in text
                and expected["parties"]["tenant"] in text
                and expected["notice_section"] in text
                and (current is None or f"${current:,.2f}" in text)
                and a.args.get("to") == "{{RECIPIENT}}"
                and "{" not in text
            ):
                found = a
                break
        checks.append({"expected": f"{variant} email citing names, {expected['notice_section']}, deadline, current rent",
                       "tool": "draft_email", "match": found is not None})

    record = next((a for a in actions if a.tool == "upsert_record"), None)
    data = record.args["data"] if record else {}
    checks.append({"expected": "tracker record with deadline, counterparty and status 'decision needed'",
                   "tool": "upsert_record",
                   "match": bool(record) and data.get("status") == expected["tracker_status"]
                   and data.get("deadline") == due.isoformat()
                   and data.get("counterparty") == expected["parties"]["landlord"]})

    exclusive = {a.group_mode for a in actions if a.tool == "draft_email"} == {"exclusive"}
    checks.append({"expected": "the two drafts are one exclusive choice", "tool": "draft_email",
                   "match": exclusive and len([a for a in actions if a.tool == "draft_email"]) == 2})
    return {
        "checks": checks,
        "match": bool(checks) and all(c["match"] for c in checks),
        "extra": [a.tool for a in actions if a.tool not in {"create_calendar_event", "draft_email", "upsert_record"}],
        "proposed": [
            {
                "tool": a.tool,
                "args": a.args,
                "source_fields": a.source_fields,
                "group": f"{a.group_key}/{a.group_mode}" if a.group_key else None,
                "policy": f"{a.policy_status}: {a.policy_reason}",
            }
            for a in actions
        ],
        "rejected": plan.rejected if plan else [],
        "notes": plan.notes if plan else [],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=str(ROOT / "fixtures/demo/lease.pdf"))
    parser.add_argument("--expected", default=str(ROOT / "fixtures/demo/lease.expected.json"))
    args = parser.parse_args()

    pdf_path, expected = Path(args.pdf), json.loads(Path(args.expected).read_text(encoding="utf-8"))
    today = date.today()
    started = time.perf_counter()
    parse_started = time.perf_counter()
    pages = parse_pdf(pdf_path.read_bytes())
    parse_ms = int((time.perf_counter() - parse_started) * 1000)
    result = run_pipeline(pages, make_parse_step(pages, parse_ms, reused=False), today=today)
    latency_ms = int((time.perf_counter() - started) * 1000)

    if result.status != "done":
        failed = [f"{s.stage}: {s.error}" for s in result.steps if s.error]
        print(f"PIPELINE {result.status.upper()}: {failed}")

    by_name = {f.name: f for f in result.fields}
    rows = []
    for name, want in expected_values(expected, today).items():
        f = by_name.get(name)
        got = f.value if f else None
        rows.append(
            {
                "field": name,
                "expected": want,
                "extracted": got,
                "match": matches(name, want, got),
                "confidence": f.confidence if f else None,
                "grounded": bool(f and f.value is not None and "ungrounded" not in f.flags),
                "flags": f.flags if f else [],
                "page": f.page_no if f else None,
                "quote": f.source_quote if f else None,
            }
        )

    want_obl = expected["obligations"][0]
    got_obl = next((o for o in result.obligations if o.kind == want_obl["kind"]), None)
    obligation = {
        "expected_due_date": want_obl["due_date"],
        "extracted_due_date": got_obl.due_date.isoformat() if got_obl else None,
        "match": bool(got_obl and got_obl.due_date.isoformat() == want_obl["due_date"]),
    }

    brief = check_brief(expected, result.brief, today)
    actions = check_actions(expected, result.plan, today, rent_today(expected, today))
    rent = by_name.get("rent_amount")
    api_calls = 1 + (result.plan.api_calls if result.plan else 0)
    summary = {
        "pdf": pdf_path.name,
        "today": today.isoformat(),
        "run_status": result.status,
        "fields_matched": f"{sum(r['match'] for r in rows)}/{len(rows)}",
        "obligation_match": obligation["match"],
        "brief_match": brief["match"],
        "actions_match": actions["match"],
        "actions_proposed": len(actions["proposed"]),
        "rent_amount_extracted": rent.value if rent else None,
        "rent_amount_flags": rent.flags if rent else [],
        "openai_calls": api_calls,
        "tokens": result.tokens,
        "cost_usd": result.cost_usd,
        "latency_ms": latency_ms,
    }

    print(f"today {today.isoformat()}")
    print(f"{'field':24} {'expected':30} {'extracted':30} match  conf  grounded flags")
    for r in rows:
        want = r["expected"]["contains"] if isinstance(r["expected"], dict) else r["expected"]
        print(
            f"{r['field']:24} {str(want)[:29]:30} {str(r['extracted'])[:29]:30} "
            f"{'yes' if r['match'] else 'NO':5}  "
            f"{'-' if r['confidence'] is None else format(r['confidence'], '.2f'):>4}  "
            f"{'yes' if r['grounded'] else 'no':8} {','.join(r['flags'])}"
        )
    print(f"\nobligation: expected {obligation['expected_due_date']}, got {obligation['extracted_due_date']} -> "
          f"{'yes' if obligation['match'] else 'NO'}")
    print("\ndecision brief (code vs independent recomputation):")
    for b in brief["lines"]:
        print(f"  {b['line']:16} expected {str(b['expected']):12} got {str(b['got']):12} -> {'yes' if b['match'] else 'NO'}")
    print(f"  every line cites source fields -> {'yes' if brief['all_cite_sources'] else 'NO'}")
    if brief["skipped"]:
        print(f"  skipped: {brief['skipped']}")
    print("\nproposed actions:")
    for a in actions["proposed"]:
        detail = a["args"].get("date") or a["args"].get("subject") or a["args"].get("key") or ""
        print(f"  {a['tool']:24} {str(detail)[:42]:42} {str(a['group'] or ''):22} [{a['policy'].split(':')[0]}]")
    for c in actions["checks"]:
        print(f"  check: {c['expected'][:78]:78} -> {'yes' if c['match'] else 'NO'}")
    if actions["extra"] or actions["rejected"]:
        print(f"  extra actions: {actions['extra']}  rejected proposals: {actions['rejected']}")
    if actions["notes"]:
        print(f"  planner notes: {actions['notes']}")
    print(f"\ntokens {summary['tokens']}  cost ${summary['cost_usd']:.5f}  latency {latency_ms} ms  "
          f"openai calls {api_calls}  fields matched {summary['fields_matched']}")

    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{pdf_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(
        json.dumps(
            {
                "summary": summary,
                "fields": rows,
                "obligation": obligation,
                "brief": brief,
                "actions": actions,
                "steps": [vars(s) for s in result.steps],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
