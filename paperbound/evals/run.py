"""Eval harness CLI. Calls the pipeline directly: no API, no database, no job queue.

    python -m paperbound.evals.run --suite all --save-baseline
    python -m paperbound.evals.run --suite synthetic --doc-type lease --variant injection
    python -m paperbound.evals.run --suite generic
    python -m paperbound.evals.run --suite cuad --limit 10

Every run is as of AS_OF (default 2026-10-22). OpenAI calls are capped (--budget, default 160 in total across runs,
counted in evals/.budget.json) and only gpt-4o-mini is allowed. Results go to evals/results/<suite>_<timestamp>.json,
the failure log to failures_<suite>_<timestamp>.json, and evals/results/latest.md is rebuilt from the newest run of
each suite. Every run is diffed against evals/baseline.json; a drop of more than 2 points is flagged.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from paperbound import config
from paperbound.evals import cuad, report, synthetic
from paperbound.evals import generic as generic_eval
from paperbound.evals.budget import DEFAULT_LIMIT, Budget, BudgetExceeded

DEFAULT_AS_OF = "2026-10-22"
CUAD_SHRUNK_N = 25
CUAD_HEADROOM = 10  # calls left for retries the counter cannot see


def synthetic_model() -> str:
    from paperbound.pipeline.extract import MODEL

    return f"{MODEL} (temperature 0)"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def run_synthetic(args, budget: Budget, as_of: date) -> dict:
    folders = synthetic.discover(args.doc_type, args.variant, args.limit)
    print(f"synthetic: {len(folders)} fixtures, {budget.remaining} OpenAI calls left")
    records, aborted = [], None
    for folder in folders:
        try:
            rec = synthetic.run_fixture(folder, as_of, budget)
        except BudgetExceeded as exc:
            aborted = str(exc)
            print(f"  STOP: {aborted}")
            break
        records.append(rec)
        bad = [r["key"] for r in rec["fields"] if not r["match"]]
        print(f"  {rec['id']:9} {rec['variant']:14} fields {sum(r['match'] for r in rec['fields'])}/{len(rec['fields'])} "
              f"oblig {'ok' if rec['obligation']['match'] else 'NO'} brief {'ok' if rec['brief']['match'] else 'NO'} "
              f"actions {'ok' if rec['actions']['match'] else 'NO'} calls {rec['calls']} {rec['latency_ms'] / 1000:.1f}s"
              + (f"  wrong: {bad[:4]}" if bad else "") + (f"  ERRORS {rec['errors']}" if rec["errors"] else ""))
    ts = stamp()
    log = report.failures_synthetic(records)
    groups = report.summarize_synthetic(records) if records else {}
    return {"suite": "synthetic", "timestamp": ts, "as_of": as_of.isoformat(), "n_docs": len(records), "groups": groups,
            "failure_modes": report.failure_modes(log), "failure_log": f"failures_synthetic_{ts}.json", "_log": log,
            "records": records, "aborted": aborted, "notes": [
                "Template-generated fixtures: 3 layouts per type, seeded values. Latency runs one document at a time."]}


def run_generic(args, budget: Budget, as_of: date) -> dict:
    folders = generic_eval.discover(args.limit)
    print(f"generic: {len(folders)} fixtures, {budget.remaining} OpenAI calls left")
    records, aborted = [], None
    for folder in folders:
        try:
            rec = generic_eval.run_fixture(folder, as_of, budget)
        except BudgetExceeded as exc:
            aborted = str(exc)
            print(f"  STOP: {aborted}")
            break
        records.append(rec)
        bad = [f"{r['category']}:{r['key']}" for r in rec["fields"] if not r["match"]]
        print(f"  {rec['id']:12} routed {'ok' if rec['classification']['match'] else 'NO'} "
              f"fields {sum(r['match'] for r in rec['fields'])}/{len(rec['fields'])} extras {len(rec['extras'])} "
              f"oblig {'ok' if rec['obligation']['match'] else 'NO'} actions {'ok' if rec['actions']['match'] else 'NO'} "
              f"calls {rec['calls']} {rec['latency_ms'] / 1000:.1f}s" + (f"  wrong: {bad[:4]}" if bad else "")
              + (f"  ERRORS {rec['errors']}" if rec["errors"] else ""))
    ts = stamp()
    log = generic_eval.failures(records)
    groups = report.summarize_generic(records) if records else {}
    return {"suite": "generic", "timestamp": ts, "as_of": as_of.isoformat(), "n_docs": len(records), "groups": groups,
            "failure_modes": report.failure_modes(log), "failure_log": f"failures_generic_{ts}.json", "_log": log,
            "records": records, "aborted": aborted, "notes": [
                "Five hand-written notices, invoices and orders: a type Paperbound has no schema for. Scored by value, not label."]}


def run_cuad(args, budget: Budget, as_of: date) -> dict | None:
    if not cuad.DATASET.exists():
        print("cuad: dataset not downloaded (evals/datasets/cuad/CUAD_v1.json), skipping")
        return None
    contracts = cuad.load_dataset()
    sample = cuad.pick_sample(contracts, 50)
    n, notes = len(sample), []
    if budget.remaining < n + CUAD_HEADROOM and args.cuad_n is None:
        n = CUAD_SHRUNK_N
        notes.append(f"Budget was tight ({budget.remaining} calls left), so the sample shrank from {len(sample)} to {n} contracts.")
    if args.cuad_n:
        n = min(args.cuad_n, n)
    if args.limit:
        n = min(n, args.limit)
    cuad.SAMPLE_FILE.write_text(json.dumps({
        "seed": cuad.SEED, "max_chars": cuad.MAX_CHARS, "sample_size": len(sample), "used": n,
        "contracts": [c["title"] for c in sample]}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"cuad: {n} contracts of a {len(sample)}-contract fixed sample, {budget.remaining} OpenAI calls left")
    rows, records, aborted = [], [], None
    for contract in sample[:n]:
        pages = cuad.paginate(contract["text"])
        calls_before, started = budget.session_calls, time.perf_counter()
        cost = tokens = 0
        error = None
        try:
            extraction = cuad.extract_generic(pages, as_of)
            fields = cuad.validate_generic(extraction.fields, pages)
            cost, tokens = extraction.cost_usd, extraction.tokens
        except BudgetExceeded as exc:
            aborted = str(exc)
            print(f"  STOP: {aborted}")
            break
        except Exception as exc:  # a failed call counts as a miss on every category
            error, fields = f"{type(exc).__name__}: {exc}", []
        contract_rows = cuad.score_contract(contract, fields)
        rows += contract_rows
        records.append({"title": contract["title"], "pages": len(pages), "chars": len(contract["text"]), "cost_usd": cost,
                        "tokens": tokens, "latency_ms": int((time.perf_counter() - started) * 1000),
                        "calls": budget.session_calls - calls_before, "error": error})
        f1s = [r["f1"] for r in contract_rows if r["f1"] is not None]
        print(f"  {contract['title'][:56]:56} labeled {len(f1s)} f1 {sum(f1s) / len(f1s) if f1s else float('nan'):.2f} "
              f"{records[-1]['latency_ms'] / 1000:.1f}s" + (f"  ERROR {error}" if error else ""))
    ts = stamp()
    log = report.failures_cuad(rows)
    return {"suite": "cuad", "timestamp": ts, "as_of": as_of.isoformat(), "n_docs": len(records), "sample_size": len(sample),
            "groups": report.summarize_cuad(rows, records) if records else {}, "failure_modes": report.failure_modes(log),
            "failure_log": f"failures_cuad_{ts}.json", "_log": log, "records": records, "rows": rows, "aborted": aborted,
            "notes": notes + ["Real contracts, span-scored: token overlap of our quote with the labeled span(s)."]}


def save(run: dict) -> None:
    report.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log = run.pop("_log")
    (report.RESULTS_DIR / run["failure_log"]).write_text(json.dumps(log, indent=1, default=str), encoding="utf-8")
    (report.RESULTS_DIR / f"{run['suite']}_{run['timestamp']}.json").write_text(json.dumps(run, indent=1, default=str), encoding="utf-8")


def rescore_file(path) -> dict:
    """Re-apply the scoring rules that a saved synthetic run's stored data supports. No model calls, no PDFs."""
    from pathlib import Path

    path = Path(path)
    run = json.loads(path.read_text(encoding="utf-8"))
    if run.get("suite") != "synthetic":
        sys.exit("--rescore only supports synthetic runs")
    for rec in run["records"]:
        expected = json.loads((synthetic.FIXTURES / rec["doc_type"] / rec["id"] / "expected.json").read_text(encoding="utf-8"))
        synthetic.rescore(rec, expected)
    log = report.failures_synthetic(run["records"])
    run["groups"], run["failure_modes"] = report.summarize_synthetic(run["records"]), report.failure_modes(log)
    run["rescored"] = ("Action number rule changed after this run (the configured certificate minimum is an allowed number); "
                       "action and injection results re-derived from the saved stray-number lists. No new model calls.")
    (report.RESULTS_DIR / run["failure_log"]).write_text(json.dumps(log, indent=1, default=str), encoding="utf-8")
    path.write_text(json.dumps(run, indent=1, default=str), encoding="utf-8")
    return run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=["synthetic", "generic", "cuad", "all"], default="all")
    parser.add_argument("--doc-type", choices=list(synthetic.DOC_TYPES), help="synthetic only")
    parser.add_argument("--variant", choices=["clean", "missing_field", "late_page", "injection"], help="synthetic only")
    parser.add_argument("--limit", type=int, help="at most this many documents per suite")
    parser.add_argument("--save-baseline", action="store_true", help="write the metrics of this run to evals/baseline.json")
    parser.add_argument("--budget", type=int, default=DEFAULT_LIMIT, help="total OpenAI calls allowed across runs")
    parser.add_argument("--cuad-n", type=int, help="contracts to score (default 50, or 25 when the budget is tight)")
    parser.add_argument("--median-baseline", nargs="+", metavar="RESULT_JSON",
                        help="re-record evals/baseline.json as the per-metric median of these saved runs (no model calls)")
    parser.add_argument("--rescore", metavar="RESULT_JSON", help="re-score a saved synthetic run with the current rules; no model calls")
    parser.add_argument("--as-of", default=os.environ.get("AS_OF") or DEFAULT_AS_OF)
    args = parser.parse_args(argv)

    if args.median_baseline:
        flat = report.median_baseline(args.median_baseline)
        runs = [json.loads(Path(f).read_text(encoding="utf-8")) for f in args.median_baseline]
        per_suite = {s: sum(r["suite"] == s for r in runs) for s in sorted({r["suite"] for r in runs})}
        report.save_baseline(flat, {
            "saved": date.today().isoformat(), "as_of": args.as_of, "model": synthetic_model(),
            "note": f"median over saved runs per suite {per_suite}: " + ", ".join(Path(f).name for f in args.median_baseline),
        }, replace=True)
        print(f"baseline re-recorded: {len(flat)} metrics, median of {per_suite}")
        return 0

    if args.rescore:
        run = rescore_file(args.rescore)
        flat = report.flat_metrics("synthetic", run["groups"])
        drops = {"synthetic": report.metrics.diff_baseline(flat, report.load_baseline())}
        report.LATEST_MD.write_text(report.render_markdown(report.latest_runs(), drops), encoding="utf-8", newline="\n")
        print(f"rescored {args.rescore}; wrote evals/results/latest.md")
        if args.save_baseline:
            report.save_baseline(flat, {"saved": datetime.now().isoformat(timespec="seconds"), "as_of": run["as_of"]})
            print("baseline updated")
        return 0

    as_of = date.fromisoformat(args.as_of)
    os.environ["AS_OF"] = as_of.isoformat()
    if not config.OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY is not set")
    budget = Budget(args.budget)
    budget.install()

    runs = []
    if args.suite in ("synthetic", "all"):
        runs.append(run_synthetic(args, budget, as_of))
    if args.suite in ("generic", "all") and not (runs and runs[-1]["aborted"]):
        runs.append(run_generic(args, budget, as_of))
    if args.suite in ("cuad", "all") and not (runs and runs[-1]["aborted"]):
        if (run := run_cuad(args, budget, as_of)) is not None:
            runs.append(run)

    baseline = report.load_baseline()
    drops: dict[str, list[dict]] = {}
    flat_all: dict[str, float] = {}
    for run in runs:
        flat = report.flat_metrics(run["suite"], run["groups"])
        flat_all |= flat
        drops[run["suite"]] = metrics_diff = report.metrics.diff_baseline(flat, baseline)
        run["baseline_drops"] = metrics_diff
        save(run)
        print(f"saved evals/results/{run['suite']}_{run['timestamp']}.json  ({run['n_docs']} docs"
              + (f", ABORTED: {run['aborted']}" if run["aborted"] else "") + ")")
    report.LATEST_MD.write_text(report.render_markdown(report.latest_runs(), drops), encoding="utf-8", newline="\n")
    print("wrote evals/results/latest.md")
    for suite, items in drops.items():
        for d in items:
            print(f"DROP over 2 points: {d['metric']} {d['baseline']:.3f} -> {d['current']:.3f} ({d['delta_points']} points)")
    if args.save_baseline and flat_all and not any(r["aborted"] for r in runs):
        report.save_baseline(flat_all, {"saved": datetime.now().isoformat(timespec="seconds"), "as_of": as_of.isoformat()})
        print("baseline saved to evals/baseline.json")
    elif args.save_baseline:
        print("baseline NOT saved: a run was aborted or produced no metrics")
    print(f"OpenAI calls this session: {budget.session_calls}; total used {budget.calls} of {budget.limit}")
    return 1 if any(r["aborted"] for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
