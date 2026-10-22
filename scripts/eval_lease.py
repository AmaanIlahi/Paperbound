"""Run the full pipeline on a lease PDF and compare against its expected JSON.

Usage: python scripts/eval_lease.py [--pdf fixtures/demo/lease.pdf] [--expected fixtures/demo/lease.expected.json]
Makes about three OpenAI calls (extraction + planner). Does not touch the database.
"""

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperbound.pipeline.parse import parse_pdf  # noqa: E402
from paperbound.pipeline.runner import make_parse_step, run_pipeline  # noqa: E402


def expected_values(expected: dict) -> dict:
    return {
        "landlord": expected["parties"]["landlord"],
        "tenant": expected["parties"]["tenant"],
        "premises_address": expected["premises"],
        "start_date": expected["start_date"],
        "end_date": expected["end_date"],
        "rent_amount": expected["rent_amount"],
        "rent_period": expected["rent_period"],
        "notice_period_days": expected["notice_period_days"],
    }


def matches(name: str, want, got) -> bool:
    if got is None:
        return False
    if name == "rent_amount":
        return abs(float(got) - float(want)) < 0.005
    if name == "premises_address":
        return fuzz.token_set_ratio(str(want), str(got)) >= 85
    if isinstance(want, str):
        return str(got).strip().rstrip(".").casefold() == want.strip().rstrip(".").casefold()
    return got == want


def check_actions(expected: dict, plan) -> dict:
    """Compare proposed actions with the expected ones. Extra actions are reported, not penalized."""
    actions = plan.actions if plan else []
    checks = []
    for want in expected.get("actions", []):
        found = None
        for a in actions:
            if a.tool != want["tool"]:
                continue
            if "date_before" in want:
                try:
                    ok = date.fromisoformat(a.args.get("date", "")) < date.fromisoformat(want["date_before"])
                except ValueError:
                    ok = False
            else:
                text = f"{a.args.get('subject', '')} {a.args.get('body', '')}".casefold()
                ok = any(word in text for word in want["mentions_any"])
            if ok:
                found = a
                break
        checks.append({"expected": want["description"], "tool": want["tool"], "match": found is not None})
    expected_tools = {w["tool"] for w in expected.get("actions", [])}
    return {
        "checks": checks,
        "match": bool(checks) and all(c["match"] for c in checks),
        "extra": [a.tool for a in actions if a.tool not in expected_tools],
        "proposed": [
            {
                "tool": a.tool,
                "args": a.args,
                "source_fields": a.source_fields,
                "policy": f"{a.policy_status}: {a.policy_reason}",
            }
            for a in actions
        ],
        "rejected": plan.rejected if plan else [],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=str(ROOT / "fixtures/demo/lease.pdf"))
    parser.add_argument("--expected", default=str(ROOT / "fixtures/demo/lease.expected.json"))
    args = parser.parse_args()

    pdf_path, expected = Path(args.pdf), json.loads(Path(args.expected).read_text(encoding="utf-8"))
    started = time.perf_counter()
    parse_started = time.perf_counter()
    pages = parse_pdf(pdf_path.read_bytes())
    parse_ms = int((time.perf_counter() - parse_started) * 1000)
    result = run_pipeline(pages, make_parse_step(pages, parse_ms, reused=False))
    latency_ms = int((time.perf_counter() - started) * 1000)

    if result.status != "done":
        failed = [f"{s.stage}: {s.error}" for s in result.steps if s.error]
        print(f"PIPELINE {result.status.upper()}: {failed}")

    by_name = {f.name: f for f in result.fields}
    rows = []
    for name, want in expected_values(expected).items():
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
            }
        )

    want_obl = expected["obligations"][0]
    got_obl = next((o for o in result.obligations if o.kind == want_obl["kind"]), None)
    obligation = {
        "expected_due_date": want_obl["due_date"],
        "extracted_due_date": got_obl.due_date.isoformat() if got_obl else None,
        "match": bool(got_obl and got_obl.due_date.isoformat() == want_obl["due_date"]),
    }

    actions = check_actions(expected, result.plan)
    rent = by_name.get("rent_amount")
    api_calls = 1 + (result.plan.api_calls if result.plan else 0)
    summary = {
        "pdf": pdf_path.name,
        "run_status": result.status,
        "fields_matched": f"{sum(r['match'] for r in rows)}/{len(rows)}",
        "obligation_match": obligation["match"],
        "actions_match": actions["match"],
        "rent_amount_extracted": rent.value if rent else None,
        "rent_amount_flags": rent.flags if rent else [],
        "openai_calls": api_calls,
        "tokens": result.tokens,
        "cost_usd": result.cost_usd,
        "latency_ms": latency_ms,
    }

    print(f"{'field':20} {'expected':32} {'extracted':32} match  conf  grounded flags")
    for r in rows:
        print(
            f"{r['field']:20} {str(r['expected'])[:31]:32} {str(r['extracted'])[:31]:32} "
            f"{'yes' if r['match'] else 'NO':5}  "
            f"{'-' if r['confidence'] is None else format(r['confidence'], '.2f'):>4}  "
            f"{'yes' if r['grounded'] else 'no':8} {','.join(r['flags'])}"
        )
    print(f"\nobligation: expected {obligation['expected_due_date']}, got {obligation['extracted_due_date']} -> "
          f"{'yes' if obligation['match'] else 'NO'}")
    print("\nproposed actions:")
    for a in actions["proposed"]:
        detail = a["args"].get("date") or a["args"].get("subject") or ""
        print(f"  {a['tool']:24} {str(detail)[:50]:50} from {','.join(a['source_fields'])} [{a['policy'].split(':')[0]}]")
    for c in actions["checks"]:
        print(f"  expected {c['tool']:24} ({c['expected']}) -> {'yes' if c['match'] else 'NO'}")
    if actions["extra"] or actions["rejected"]:
        print(f"  extra actions: {actions['extra']}  rejected proposals: {actions['rejected']}")
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
