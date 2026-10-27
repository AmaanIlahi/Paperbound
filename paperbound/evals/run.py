"""Eval harness CLI. Calls the pipeline directly: no API, no database, no job queue.

    python -m paperbound.evals.run --suite all --save-baseline
    python -m paperbound.evals.run --suite synthetic --doc-type lease --variant injection
    python -m paperbound.evals.run --suite generic
    python -m paperbound.evals.run --suite cuad --limit 10
    python -m paperbound.evals.run --suite rent_roll        # also t12, crosscheck, or cre for the three

Every run is as of AS_OF (default 2026-10-22). OpenAI calls are capped per run (--budget, default 500; evals/.budget.json
keeps a cumulative count as a report only) and only gpt-4o-mini is allowed. Results go to evals/results/<suite>_<timestamp>.json,
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
from paperbound.evals import cre as cre_eval
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


CRE_SCORERS = {
    "rent_roll": (cre_eval.score_rent_roll, report.summarize_rent_roll, cre_eval.failures_rent_roll,
                  "Template-generated dev rent rolls in four layouts. Not a measure of accuracy on real documents."),
    "t12": (cre_eval.score_t12, report.summarize_t12, cre_eval.failures_t12,
            "Template-generated dev T-12 statements in three layouts. Not a measure of accuracy on real documents."),
}


def run_cre(suite: str, args, budget: Budget, as_of: date, root: Path = cre_eval.FIXTURES, label: str = "dev") -> dict:
    """Rent roll, T-12 or cross-check over the fixtures under `root` (the dev set, or evals/heldout with --heldout)."""
    records, aborted = [], None
    if suite == "crosscheck":
        print(f"crosscheck ({label}): pairs and negative controls, {budget.remaining} OpenAI calls left")
        try:
            records = cre_eval.run_crosscheck(as_of, budget, root, args.limit)
        except BudgetExceeded as exc:
            aborted = str(exc)
            print(f"  STOP: {aborted}")
        for r in records:
            print(f"  {r['id']:22} {r['kind']:9} flags {r['got_flags']} expected {r['expected_flags']} {'ok' if r['match'] else 'NO'}")
        if not records and not aborted:
            print("  no pairs found")
        log, groups = report_failures(suite, records), (report.summarize_crosscheck(records) if records else {})
        notes = ["Five rent roll + T-12 pairs and five negative controls. No model call of its own: it reuses the rent roll and T-12 runs."]
    else:
        scorer, summarize, failures, note = CRE_SCORERS[suite]
        folders = cre_eval.discover(suite, args.limit, root)
        print(f"{suite} ({label}): {len(folders)} fixtures, {budget.remaining} OpenAI calls left")
        for folder in folders:
            try:
                rec = scorer(cre_eval.run_document(folder, as_of, budget))
            except BudgetExceeded as exc:
                aborted = str(exc)
                print(f"  STOP: {aborted}")
                break
            records.append(rec)
            if suite == "rent_roll":
                print(f"  {rec['id']:10} {str(rec['layout']):3} map {sum(m['ok'] for m in rec['mapping'])}/{len(rec['mapping'])} "
                      f"cells {sum(c['ok'] for c in rec['cells'])}/{len(rec['cells'])} totals {'flagged' if rec['totals']['flagged'] else 'clean'}"
                      f"{' (planted)' if rec['totals']['planted'] else ''} metrics {sum(m['ok'] for m in rec['metrics'])}/{len(rec['metrics'])} "
                      f"calls {rec['calls']} {rec['latency_ms'] / 1000:.1f}s" + (f"  ERRORS {rec['errors']}" if rec["errors"] else ""))
            else:
                print(f"  {rec['id']:10} {str(rec['layout']):3} categories {sum(ln['category_ok'] for ln in rec['lines'])}/{len(rec['lines'])} "
                      f"cells {sum(ln['months_ok'] and ln['total_ok'] for ln in rec['lines'])}/{len(rec['lines'])} detection {'ok' if rec['detection']['match'] else 'NO'} "
                      f"anomalies {'ok' if rec['anomalies']['match'] else 'NO'} calls {rec['calls']} {rec['latency_ms'] / 1000:.1f}s"
                      + (f"  ERRORS {rec['errors']}" if rec["errors"] else ""))
        log, groups, notes = failures(records), (summarize(records) if records else {}), [note]
    ts = stamp()
    prefix = "heldout_" if label == "heldout" else ""
    return {"suite": suite, "timestamp": ts, "as_of": as_of.isoformat(), "n_docs": len(records), "groups": groups,
            "failure_modes": report.failure_modes(log), "failure_log": f"failures_{prefix}{suite}_{ts}.json", "_log": log, "records": records,
            "aborted": aborted, "notes": notes, "dataset": label, "file_prefix": prefix}


def report_failures(suite: str, records: list[dict]) -> list[dict]:
    return cre_eval.failures_crosscheck(records)


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


def failed_everywhere(run: dict) -> bool:
    """True when every document of a suite ended in an error (no credits, no network, a bad key): there is nothing to report,
    and saving it would replace the newest good result with zeros."""
    records = run.get("records") or []
    if run["suite"] == "crosscheck":
        return False  # it makes no calls of its own; its inputs are the rent roll and T-12 runs
    key = "error" if run["suite"] == "cuad" else "errors"
    return bool(records) and all(r.get(key) for r in records)


def save(run: dict) -> None:
    report.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log = run.pop("_log")
    (report.RESULTS_DIR / run["failure_log"]).write_text(json.dumps(log, indent=1, default=str), encoding="utf-8")
    (report.RESULTS_DIR / f"{run.get('file_prefix', '')}{run['suite']}_{run['timestamp']}.json").write_text(json.dumps(run, indent=1, default=str), encoding="utf-8")


BASELINE_OPTIONS = ("save_baseline", "median_baseline", "merge_median_baseline", "rescore")


def run_heldout(args) -> int:
    """Run only the held-out set (evals/heldout): rent roll, T-12 and cross-check, reported apart, never touching the baseline."""
    from paperbound.evals import labels

    refused = [name for name in BASELINE_OPTIONS if getattr(args, name)]
    if refused:
        sys.exit("--heldout never updates or reads the baseline, so it cannot be combined with --" + ", --".join(n.replace("_", "-") for n in refused))
    if args.suite not in ("all", "cre", *report.CRE_SUITES):
        sys.exit("--heldout covers the rent roll, T-12 and cross-check suites only (use --suite all, cre, rent_roll, t12 or crosscheck)")
    found = labels.find_labels(cre_eval.HELDOUT)
    if not found:
        print("no held-out label files found in evals/heldout/{rent_roll,t12,pairs} (see the README in each folder). Nothing was run.")
        return 0
    bad = {path: r for _, path in found if not (r := labels.validate(path)).ok}
    if bad:
        for path, r in bad.items():
            print(f"ERROR {path}")
            for message in r.errors[:6]:
                print(f"   {message}")
        print(f"\n{len(bad)} of {len(found)} label file(s) are incomplete. Fix them (python scripts/check_labels.py) before running; no model call was made.")
        return 2
    as_of = date.fromisoformat(args.as_of)
    os.environ["AS_OF"] = as_of.isoformat()
    if not config.OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY is not set")
    budget = Budget(args.budget)
    budget.install()
    runs = []
    for suite in report.CRE_SUITES:
        if args.suite in (suite, "cre", "all") and not (runs and runs[-1]["aborted"]):
            runs.append(run_cre(suite, args, budget, as_of, root=cre_eval.HELDOUT, label="heldout"))
    runs = [r for r in runs if r["n_docs"]]
    unusable = [r for r in runs if failed_everywhere(r)]
    for run in unusable:
        first = next(iter(run["records"]), {})
        print(f"NOT SAVED: every held-out {run['suite']} document failed (first error: {str((first.get('errors') or [''])[0])[:160]}).")
    runs = [r for r in runs if r not in unusable]
    for run in runs:
        save(run)
        print(f"saved evals/results/heldout_{run['suite']}_{run['timestamp']}.json  ({run['n_docs']} documents"
              + (f", ABORTED: {run['aborted']}" if run["aborted"] else "") + ")")
    if runs:
        (report.RESULTS_DIR / "heldout_latest.md").write_text(report.render_heldout_markdown(runs), encoding="utf-8", newline="\n")
        print("wrote evals/results/heldout_latest.md")
    print("HELD-OUT: reported apart, no baseline comparison, baseline untouched. Do not change code in response to these results.")
    print(f"OpenAI calls this run: {budget.session_calls} of {budget.limit} allowed")
    return 2 if unusable else (1 if any(r["aborted"] for r in runs) else 0)


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
    parser.add_argument("--suite", choices=["synthetic", "generic", "cuad", "rent_roll", "t12", "crosscheck", "cre", "all"], default="all")
    parser.add_argument("--doc-type", choices=list(synthetic.DOC_TYPES), help="synthetic only")
    parser.add_argument("--variant", choices=["clean", "missing_field", "late_page", "injection"], help="synthetic only")
    parser.add_argument("--limit", type=int, help="at most this many documents per suite")
    parser.add_argument("--save-baseline", action="store_true", help="write the metrics of this run to evals/baseline.json")
    parser.add_argument("--budget", type=int, default=DEFAULT_LIMIT, help="OpenAI calls allowed in this run (the cumulative count in evals/.budget.json is only reported)")
    parser.add_argument("--cuad-n", type=int, help="contracts to score (default 50, or 25 when the budget is tight)")
    parser.add_argument("--median-baseline", nargs="+", metavar="RESULT_JSON",
                        help="re-record evals/baseline.json as the per-metric median of these saved runs (no model calls)")
    parser.add_argument("--merge-median-baseline", nargs="+", metavar="RESULT_JSON",
                        help="add the per-metric median of these saved runs to evals/baseline.json, replacing only the keys of their suites (no model calls)")
    parser.add_argument("--heldout", action="store_true",
                        help="run only evals/heldout (rent roll, T-12, cross-check): reported apart, never compared with or written to the baseline")
    parser.add_argument("--rescore", metavar="RESULT_JSON", help="re-score a saved synthetic run with the current rules; no model calls")
    parser.add_argument("--as-of", default=os.environ.get("AS_OF") or DEFAULT_AS_OF)
    args = parser.parse_args(argv)

    if args.heldout:
        return run_heldout(args)

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

    if args.merge_median_baseline:
        keys = report.merge_median_baseline(args.merge_median_baseline, saved=date.today().isoformat())
        print(f"baseline updated: {len(keys)} metrics merged, all other keys untouched")
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
    for suite in report.CRE_SUITES:
        if (args.suite in (suite, "cre", "all")) and not (runs and runs[-1]["aborted"]):
            runs.append(run_cre(suite, args, budget, as_of))

    baseline = report.load_baseline()
    drops: dict[str, list[dict]] = {}
    flat_all: dict[str, float] = {}
    unusable = [r for r in runs if failed_everywhere(r)]
    for run in unusable:
        first = next(iter(run["records"]), {})
        error = (first.get("errors") or [first.get("error")] or [""])[0]
        print(f"NOT SAVED: every {run['suite']} document failed (first error: {str(error)[:160]}). "
              "Nothing was written to evals/results and the baseline was not touched.")
    runs = [r for r in runs if r not in unusable]
    for run in runs:
        flat = report.flat_metrics(run["suite"], run["groups"])
        flat_all |= flat
        drops[run["suite"]] = metrics_diff = report.metrics.diff_baseline(flat, baseline)
        run["baseline_drops"] = metrics_diff
        save(run)
        print(f"saved evals/results/{run['suite']}_{run['timestamp']}.json  ({run['n_docs']} docs"
              + (f", ABORTED: {run['aborted']}" if run["aborted"] else "") + ")")
    if runs:
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
    print(f"OpenAI calls this run: {budget.session_calls} of {budget.limit} allowed (cumulative ledger: {budget.calls}, report only)")
    return 2 if unusable else (1 if any(r["aborted"] for r in runs) else 0)


if __name__ == "__main__":
    sys.exit(main())
