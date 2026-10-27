"""Evals of the commercial real estate pack: rent roll, T-12 and rent roll <-> T-12 cross-check.

Each document goes through the real pipeline (`run_pipeline`: classify, extract with the one mapping call, validate, decide,
act). The only model calls are the classifier (when the heuristics are unsure) and the column or label mapping. Scoring is
against the truth a fixture carries:

  rent roll   header-mapping accuracy, cell accuracy (every mapped cell of every row, parsed by code), the totals check
              (flagged exactly when the totals were planted wrong), metric accuracy (occupancy, total rent, average rent per sf,
              WALT, expirations), reminders;
  T-12        line-category accuracy, cell accuracy (months and total as printed), NOI exact match, the detection of planted
              problems (line sums, subtotals, NOI, capex in the operating expenses) and of month anomalies, as precision and recall;
  cross-check the five dev pairs (the planted rent income mismatch must be found, the others must pass) and five negative
              controls (a rent roll against the T-12 of another property must give a property mismatch).

The usable view sits beside every plain metric: a cell, row or line carrying a blocking flag counts as abstained, because the
app shows it with its flag but never uses it. Fixtures here are the DEV set (evals/fixtures/{rent_roll,t12,pairs}); the
held-out set (evals/heldout) is read by the same scorers but only through `--heldout`, and never updates the baseline.
"""

import json
import time
from datetime import date
from pathlib import Path

from paperbound import config
from paperbound.evals import metrics
from paperbound.pipeline import crosscheck as crosscheck_mod
from paperbound.pipeline import rent_roll as rr
from paperbound.pipeline.parse import parse_pdf
from paperbound.pipeline.runner import make_parse_step, run_pipeline
from paperbound.pipeline.validate import BLOCKING_FLAGS

FIXTURES = config.ROOT / "evals" / "fixtures"
HELDOUT = config.ROOT / "evals" / "heldout"
KINDS = ("rent_roll", "t12")
MONEY_TOL = 0.01
RATIO_TOL = 0.0005
YEARS_TOL = 0.01  # WALT, in years

# Running one document twice would double the model calls, so a document run is kept for the life of the process (the
# cross-check reuses the rent roll and T-12 runs of the same fixtures).
_RUNS: dict[tuple[str, str], dict] = {}


def clear_cache() -> None:
    _RUNS.clear()


def discover(kind: str, limit: int | None = None, root: Path = FIXTURES) -> list[Path]:
    folders = sorted(p for p in (root / kind).glob("*/") if (p / "expected.json").exists())
    return folders[:limit] if limit else folders


def blocked(flags) -> bool:
    return bool(set(flags or ()) & BLOCKING_FLAGS)


def _truth(folder: Path) -> dict:
    return json.loads((folder / "expected.json").read_text(encoding="utf-8"))


def run_document(folder: Path, as_of: date, budget=None) -> dict:
    """Run one fixture through the pipeline. Returns the pipeline result with timing and the number of model calls."""
    key = (folder.parent.name, str(folder))
    if key in _RUNS:
        return _RUNS[key]
    truth = _truth(folder)
    calls_before = budget.session_calls if budget else 0
    started = time.perf_counter()
    pdf = (folder / truth["pdf"]).read_bytes()
    pages = parse_pdf(pdf)
    parse_ms = int((time.perf_counter() - started) * 1000)
    result = run_pipeline(pages, make_parse_step(pages, parse_ms, reused=False), today=as_of, pdf=pdf)
    out = {"truth": truth, "result": result, "latency_ms": int((time.perf_counter() - started) * 1000),
           "calls": (budget.session_calls - calls_before) if budget else None}
    _RUNS[key] = out
    return out


def _by_name(result) -> dict:
    return {f.name: f for f in result.fields}


def _classification(result, expected: str) -> dict:
    cls = result.classification
    return {"expected": expected, "got": result.doc_type, "method": cls.method if cls else None, "match": result.doc_type == expected}


# ---------------------------------------------------------------------------------------------------------------
# rent roll
# ---------------------------------------------------------------------------------------------------------------

def _same_cell(name: str, want, got) -> bool:
    if name in ("sqft", "monthly_rent", "annual_rent", "rent_psf"):
        return want is None and got is None or (want is not None and got is not None and abs(float(want) - float(got)) <= MONEY_TOL)
    if name in ("lease_start", "lease_end"):
        return want == got
    if name == "tenant":
        if want is None:  # a vacant unit: no tenant, or the word for it
            return got is None or rr.is_vacant_text(got) or bool(rr.BLANK_RE.match(got or ""))
        return got is not None and metrics.norm_text(want) == metrics.norm_text(got)
    return (want or "").strip() == (got or "").strip()


def score_mapping(truth: dict, fields: dict) -> list[dict]:
    got = {c["column"]: c["field"] for c in (fields["mapping"].value["columns"] if "mapping" in fields and isinstance(fields["mapping"].value, dict) else [])}
    headers = truth["headers"]
    return [{"column": int(col), "header": headers[int(col)] if int(col) < len(headers) else None, "expected": want, "got": got.get(int(col)),
             "ok": got.get(int(col)) == want} for col, want in sorted(truth["mapping"].items(), key=lambda kv: int(kv[0]))]


def score_cells(truth: dict, fields: dict) -> list[dict]:
    rows = [fields.get(f"row_{i}") for i in range(1, len(truth["rows"]) + 1)]
    out = []
    for i, (want_row, got) in enumerate(zip(truth["rows"], rows), start=1):
        value = got.value if got is not None and isinstance(got.value, dict) else {}
        flags = got.flags if got is not None else []
        for name, want in want_row["cells"].items():
            have = value.get(name)
            out.append({"row": i, "field": name, "expected": want, "got": have, "ok": got is not None and _same_cell(name, want, have),
                        "blocked": blocked(flags) or got is None, "flags": flags,
                        "page_ok": (got.page_no == want_row["page"]) if got is not None else False, "has_bbox": bool(got and got.bbox)})
    return out


EXPECTED_METRICS = (
    ("occupancy_units", "occupancy_units", RATIO_TOL), ("occupancy_sqft", "occupancy_sqft", RATIO_TOL),
    ("total_annual_rent", "total_annual_rent", MONEY_TOL), ("avg_rent_psf", "avg_rent_psf", MONEY_TOL),
    ("walt_rent", "walt_rent_years", YEARS_TOL), ("walt_sqft", "walt_sqft_years", YEARS_TOL),
)


def score_rent_roll_metrics(truth: dict, result) -> list[dict]:
    lines = {ln.key: ln for ln in result.brief.lines} if result.brief else {}
    want = truth.get("metrics") or {}
    out = []
    for key, expected_key, tol in EXPECTED_METRICS:
        if want.get(expected_key) is None:
            continue
        got = lines[key].value if key in lines else None
        out.append({"metric": key, "expected": want[expected_key], "got": got, "ok": got is not None and abs(float(got) - want[expected_key]) <= tol})
    if "expirations_by_year" in want:
        got = {k[len("expirations_"):]: v.value for k, v in lines.items() if k.startswith("expirations_")}
        expected = {y: v["count"] for y, v in want["expirations_by_year"].items()}
        out.append({"metric": "expirations_by_year", "expected": expected, "got": got, "ok": got == expected})
    if "expiring_24_months" in want:
        got = lines["expiring_24m"].value if "expiring_24m" in lines else None
        out.append({"metric": "expiring_24m", "expected": want["expiring_24_months"], "got": got, "ok": got == want["expiring_24_months"]})
    return out


def score_rent_roll(run: dict) -> dict:
    truth, result = run["truth"], run["result"]
    fields = _by_name(result)
    planted = bool(truth.get("planted", {}).get("totals_mismatch"))
    totals = fields.get("totals")
    flagged = {f.name: sorted(f.flags) for f in result.fields if f.flags}
    expected_flags = {"totals": ["totals_mismatch"]} if planted else {}
    cells = score_cells(truth, fields)
    actions = result.plan.actions if result.plan else []
    reminders = [a for a in actions if a.tool == "create_calendar_event"]
    want_reminders = (truth.get("metrics") or {}).get("expiring_24_months")
    review = [a for a in actions if a.tool == "flag_for_review"]
    return {
        "id": truth["id"], "doc_type": "rent_roll", "layout": truth.get("layout"), "status": result.status,
        "errors": [f"{s.stage}: {s.error}" for s in result.steps if s.error],
        "classification": _classification(result, "rent_roll"),
        "mapping": score_mapping(truth, fields), "cells": cells,
        "rows": {"expected": len(truth["rows"]), "got": sum(1 for n in fields if n.startswith("row_"))},
        "totals": {"planted": planted, "flagged": bool(totals and "totals_mismatch" in totals.flags), "present": totals is not None,
                   "stated_ok": bool(totals) and all(abs(float(totals.value.get(k) or 0) - v) <= MONEY_TOL for k, v in (truth.get("totals") or {}).items())},
        "flags": {"expected": expected_flags, "got": flagged, "match": flagged == expected_flags},
        "metrics": score_rent_roll_metrics(truth, result),
        "actions": {"reminders": len(reminders), "expected_reminders": want_reminders, "review_flag": bool(review), "expected_review": planted,
                    "match": (want_reminders is None or len(reminders) == min(want_reminders, 25)) and bool(review) == planted,
                    "all_pending": all(a.policy_status == "pending" for a in actions)},
        "cost_usd": result.cost_usd, "tokens": result.tokens, "latency_ms": run["latency_ms"], "calls": run["calls"],
    }


def rows_exact(rec: dict) -> int:
    """Rows of a scored rent roll whose every expected cell is right."""
    bad = {c["row"] for c in rec["cells"] if not c["ok"]}
    return rec["rows"]["expected"] - len(bad)


def summarize_rent_roll(records: list[dict]) -> dict:
    cells = [c for r in records for c in r["cells"]]
    mapping = [m for r in records for m in r["mapping"]]
    mets = [m for r in records for m in r["metrics"]]
    rate = metrics.rate
    unblocked = [c for c in cells if not c["blocked"]]
    out = {
        "n_docs": len(records), "n_cells": len(cells), "n_columns": len(mapping),
        "classification_accuracy": rate(sum(r["classification"]["match"] for r in records), len(records)),
        "header_mapping_accuracy": rate(sum(m["ok"] for m in mapping), len(mapping)),
        "header_mapping_exact_docs": rate(sum(all(m["ok"] for m in r["mapping"]) for r in records), len(records)),
        "row_count_correct": rate(sum(r["rows"]["expected"] == r["rows"]["got"] for r in records), len(records)),
        "cell_accuracy": rate(sum(c["ok"] for c in cells), len(cells)),
        "row_exact_rate": rate(sum(rows_exact(r) for r in records), sum(r["rows"]["expected"] for r in records)),
        "page_accuracy": rate(sum(c["page_ok"] for c in cells), len(cells)),
        "bbox_rate": rate(sum(c["has_bbox"] for c in cells), len(cells)),
        "totals_check_correct": rate(sum(r["totals"]["planted"] == r["totals"]["flagged"] for r in records), len(records)),
        "totals_planted_found": rate(sum(r["totals"]["flagged"] for r in records if r["totals"]["planted"]), sum(r["totals"]["planted"] for r in records)),
        "totals_false_alarms": sum(r["totals"]["flagged"] and not r["totals"]["planted"] for r in records),
        "flags_exact": rate(sum(r["flags"]["match"] for r in records), len(records)),
        "metric_accuracy": rate(sum(m["ok"] for m in mets), len(mets)),
        "metric_docs_correct": rate(sum(all(m["ok"] for m in r["metrics"]) for r in records), len(records)),
        "action_correct": rate(sum(r["actions"]["match"] for r in records), len(records)),
        # usable view: a blocked cell is abstained (shown with its flag, never used)
        "usable_view_cell_accuracy": rate(sum(c["ok"] and not c["blocked"] for c in cells), len(cells)),
        "usable_view_wrong_rate": rate(sum(not c["ok"] for c in unblocked), len(unblocked)),
        "usable_view_abstained": sum(c["blocked"] for c in cells),
        "cost_usd_mean": metrics.mean([r["cost_usd"] for r in records]), "cost_usd_total": sum(r["cost_usd"] for r in records),
        "tokens_mean": metrics.mean([r["tokens"] for r in records]),
        "latency_ms_mean": metrics.mean([r["latency_ms"] for r in records]), "latency_ms_p95": metrics.p95([r["latency_ms"] for r in records]),
        "calls_total": sum(r["calls"] or 0 for r in records),
    }
    for name in ("occupancy_units", "occupancy_sqft", "total_annual_rent", "avg_rent_psf", "walt_rent", "walt_sqft", "expirations_by_year", "expiring_24m"):
        of = [m for m in mets if m["metric"] == name]
        out[f"metric_{name}"] = rate(sum(m["ok"] for m in of), len(of))
    layouts = sorted({r["layout"] for r in records if r["layout"]})
    out["by_layout"] = {
        lay: {"n_docs": sum(r["layout"] == lay for r in records),
              "cell_accuracy": rate(sum(c["ok"] for r in records if r["layout"] == lay for c in r["cells"]), sum(len(r["cells"]) for r in records if r["layout"] == lay)),
              "header_mapping_accuracy": rate(sum(m["ok"] for r in records if r["layout"] == lay for m in r["mapping"]), sum(len(r["mapping"]) for r in records if r["layout"] == lay))}
        for lay in layouts}
    return out


def failures_rent_roll(records: list[dict]) -> list[dict]:
    log = []

    def add(rec, kind, **kw):
        log.append({"suite": "rent_roll", "fixture": rec["id"], "doc_type": "rent_roll", "variant": rec.get("layout"), "kind": kind, **kw})

    for rec in records:
        if rec["status"] != "done":
            add(rec, "pipeline_error", expected="status done", extracted=rec["status"], detail=rec["errors"])
        if not rec["classification"]["match"]:
            add(rec, "classification", expected="rent_roll", extracted=rec["classification"]["got"])
        for m in rec["mapping"]:
            if not m["ok"]:
                add(rec, "header_mapping", field=m["header"], expected=m["expected"], extracted=m["got"])
        for c in rec["cells"]:
            if not c["ok"]:
                add(rec, "cell", field=f"row {c['row']} {c['field']}", expected=c["expected"], extracted=c["got"], flags=c["flags"])
        if rec["totals"]["planted"] != rec["totals"]["flagged"]:
            add(rec, "totals_check", expected="flagged" if rec["totals"]["planted"] else "clean", extracted="flagged" if rec["totals"]["flagged"] else "clean")
        if not rec["flags"]["match"]:
            add(rec, "flags", expected=rec["flags"]["expected"], extracted=rec["flags"]["got"])
        for m in rec["metrics"]:
            if not m["ok"]:
                add(rec, "metric", field=m["metric"], expected=m["expected"], extracted=m["got"])
        if not rec["actions"]["match"]:
            add(rec, "action", expected={"reminders": rec["actions"]["expected_reminders"], "review": rec["actions"]["expected_review"]},
                extracted={"reminders": rec["actions"]["reminders"], "review": rec["actions"]["review_flag"]})
    return log


# ---------------------------------------------------------------------------------------------------------------
# T-12
# ---------------------------------------------------------------------------------------------------------------

def score_lines(truth: dict, fields: dict) -> list[dict]:
    out = []
    for i, want in enumerate(truth["lines"], start=1):
        got = fields.get(f"line_{i}")
        value = got.value if got is not None and isinstance(got.value, dict) else {}
        flags = got.flags if got is not None else []
        months_ok = got is not None and value.get("months") is not None and len(value["months"]) == len(want["months"]) and all(
            a is not None and abs(a - b) <= MONEY_TOL for a, b in zip(value["months"], want["months"]))
        total_ok = got is not None and value.get("total_stated") is not None and abs(value["total_stated"] - want["total"]) <= MONEY_TOL
        out.append({"line": i, "label": want["label"], "label_ok": got is not None and value.get("label") == want["label"],
                    "expected_category": want["category"], "category": value.get("category"), "category_ok": value.get("category") == want["category"],
                    "months_ok": months_ok, "total_ok": total_ok, "blocked": blocked(flags) or got is None, "flags": flags,
                    "page_ok": (got.page_no == want["page"]) if got is not None else False})
    return out


def flag_pairs(fields: dict, labels: dict[str, str] | None = None) -> set[tuple[str, str]]:
    """{(line label, flag)} for every flag a line carries."""
    return {(f.value["label"], flag) for n, f in fields.items() if n.startswith("line_") and isinstance(f.value, dict) for flag in f.flags}


def score_t12(run: dict) -> dict:
    truth, result = run["truth"], run["result"]
    fields = _by_name(result)
    lines = score_lines(truth, fields)
    expected = {(label, flag) for label, flags in truth.get("expected_flags", {}).items() for flag in flags}
    got = flag_pairs(fields)
    expected_mismatch = {p for p in expected if p[1] != "month_anomaly"}
    got_mismatch = {p for p in got if p[1] != "month_anomaly"}
    expected_anom = {p for p in expected if p[1] == "month_anomaly"}
    got_anom = {p for p in got if p[1] == "month_anomaly"}
    lines_by = {ln.key: ln for ln in result.brief.lines} if result.brief else {}
    e = truth.get("expected") or {}
    # a statement whose planted problem removes a line from the sums cannot match the true totals: those are scored as detection only
    comparable = not truth.get("planted", {}).get("line_sum_mismatch")
    figures = []
    if comparable and e:
        for key, want, tol in (("egi", e.get("egi"), MONEY_TOL), ("total_opex", e.get("opex"), MONEY_TOL), ("noi", e.get("noi"), MONEY_TOL),
                               ("expense_ratio", e.get("expense_ratio"), RATIO_TOL)):
            if want is None:
                continue
            have = lines_by[key].value if key in lines_by else None
            figures.append({"metric": key, "expected": want, "got": have, "ok": have is not None and abs(float(have) - want) <= tol})
        if e.get("noi_monthly"):
            have = lines_by["noi_trend"].value if "noi_trend" in lines_by else None
            figures.append({"metric": "noi_trend", "expected": e["noi_monthly"], "got": have,
                            "ok": have is not None and len(have) == len(e["noi_monthly"]) and all(abs(a - b) <= MONEY_TOL for a, b in zip(have, e["noi_monthly"]))})
        for cat, want in (e.get("opex_by_category") or {}).items():
            have = lines_by[f"opex_{cat}"].value if f"opex_{cat}" in lines_by else None
            figures.append({"metric": f"opex_{cat}", "expected": want, "got": have, "ok": have is not None and abs(float(have) - want) <= MONEY_TOL})
    columns = fields.get("columns")
    actions = result.plan.actions if result.plan else []
    return {
        "id": truth["id"], "doc_type": "t12", "layout": truth.get("layout"), "status": result.status,
        "errors": [f"{s.stage}: {s.error}" for s in result.steps if s.error],
        "classification": _classification(result, "t12"),
        "months_ok": bool(columns and isinstance(columns.value, dict) and columns.value.get("months") == truth["months"]),
        "lines": lines, "n_lines": {"expected": len(truth["lines"]), "got": sum(1 for n in fields if n.startswith("line_"))},
        "figures": figures, "noi_comparable": comparable,
        "detection": {"expected": sorted(expected_mismatch), "got": sorted(got_mismatch), "tp": len(expected_mismatch & got_mismatch),
                      "fp": len(got_mismatch - expected_mismatch), "fn": len(expected_mismatch - got_mismatch), "match": expected_mismatch == got_mismatch},
        "anomalies": {"expected": sorted(expected_anom), "got": sorted(got_anom), "tp": len(expected_anom & got_anom),
                      "fp": len(got_anom - expected_anom), "fn": len(expected_anom - got_anom), "match": expected_anom == got_anom},
        "actions": {"review_flag": any(a.tool == "flag_for_review" for a in actions), "expected_review": bool(expected),
                    "match": any(a.tool == "flag_for_review" for a in actions) == bool(expected) and all(a.tool == "flag_for_review" for a in actions)},
        "cost_usd": result.cost_usd, "tokens": result.tokens, "latency_ms": run["latency_ms"], "calls": run["calls"],
    }


def summarize_t12(records: list[dict]) -> dict:
    lines = [ln for r in records for ln in r["lines"]]
    figures = [f for r in records for f in r["figures"]]
    rate = metrics.rate
    det = {k: sum(r["detection"][k] for r in records) for k in ("tp", "fp", "fn")}
    anom = {k: sum(r["anomalies"][k] for r in records) for k in ("tp", "fp", "fn")}
    noi_docs = [r for r in records if r["noi_comparable"]]
    noi = [next((f for f in r["figures"] if f["metric"] == "noi"), None) for r in noi_docs]
    unblocked = [ln for ln in lines if not ln["blocked"]]
    cell_ok = lambda ln: ln["months_ok"] and ln["total_ok"]  # noqa: E731
    return {
        "n_docs": len(records), "n_lines": len(lines),
        "classification_accuracy": rate(sum(r["classification"]["match"] for r in records), len(records)),
        "month_headers_correct": rate(sum(r["months_ok"] for r in records), len(records)),
        "line_count_correct": rate(sum(r["n_lines"]["expected"] == r["n_lines"]["got"] for r in records), len(records)),
        "line_category_accuracy": rate(sum(ln["category_ok"] for ln in lines), len(lines)),
        "category_exact_docs": rate(sum(all(ln["category_ok"] for ln in r["lines"]) for r in records), len(records)),
        "cell_accuracy": rate(sum(cell_ok(ln) for ln in lines), len(lines)),
        "page_accuracy": rate(sum(ln["page_ok"] for ln in lines), len(lines)),
        "noi_exact_match": rate(sum(bool(n and n["ok"]) for n in noi), len(noi)),
        "figure_accuracy": rate(sum(f["ok"] for f in figures), len(figures)),
        "mismatch_detection_recall": rate(det["tp"], det["tp"] + det["fn"]),
        "mismatch_detection_precision": rate(det["tp"], det["tp"] + det["fp"]),
        "mismatch_detection_exact_docs": rate(sum(r["detection"]["match"] for r in records), len(records)),
        "anomaly_recall": rate(anom["tp"], anom["tp"] + anom["fn"]),
        "anomaly_precision": rate(anom["tp"], anom["tp"] + anom["fp"]),
        "anomaly_exact_docs": rate(sum(r["anomalies"]["match"] for r in records), len(records)),
        "action_correct": rate(sum(r["actions"]["match"] for r in records), len(records)),
        "usable_view_category_accuracy": rate(sum(ln["category_ok"] and not ln["blocked"] for ln in lines), len(lines)),
        "usable_view_wrong_rate": rate(sum(not ln["category_ok"] for ln in unblocked), len(unblocked)),
        "usable_view_abstained": sum(ln["blocked"] for ln in lines),
        "n_mismatch_expected": det["tp"] + det["fn"], "n_mismatch_false_alarms": det["fp"],
        "cost_usd_mean": metrics.mean([r["cost_usd"] for r in records]), "cost_usd_total": sum(r["cost_usd"] for r in records),
        "tokens_mean": metrics.mean([r["tokens"] for r in records]),
        "latency_ms_mean": metrics.mean([r["latency_ms"] for r in records]), "latency_ms_p95": metrics.p95([r["latency_ms"] for r in records]),
        "calls_total": sum(r["calls"] or 0 for r in records),
    }


def failures_t12(records: list[dict]) -> list[dict]:
    log = []

    def add(rec, kind, **kw):
        log.append({"suite": "t12", "fixture": rec["id"], "doc_type": "t12", "variant": rec.get("layout"), "kind": kind, **kw})

    for rec in records:
        if rec["status"] != "done":
            add(rec, "pipeline_error", expected="status done", extracted=rec["status"], detail=rec["errors"])
        if not rec["classification"]["match"]:
            add(rec, "classification", expected="t12", extracted=rec["classification"]["got"])
        if not rec["months_ok"]:
            add(rec, "month_headers", expected="12 months as printed", extracted=None)
        for ln in rec["lines"]:
            if not ln["category_ok"]:
                add(rec, "line_category", field=ln["label"], expected=ln["expected_category"], extracted=ln["category"], flags=ln["flags"])
            if not (ln["months_ok"] and ln["total_ok"]):
                add(rec, "line_cells", field=ln["label"], expected="months and total as printed", extracted="differs", flags=ln["flags"])
        for f in rec["figures"]:
            if not f["ok"]:
                add(rec, "figure", field=f["metric"], expected=f["expected"], extracted=f["got"])
        for kind, key in (("detection", "mismatch"), ("anomalies", "anomaly")):
            d = rec[kind]
            for item in set(map(tuple, d["expected"])) - set(map(tuple, d["got"])):
                add(rec, f"{key}_missed", field=item[0], expected=item[1], extracted=None)
            for item in set(map(tuple, d["got"])) - set(map(tuple, d["expected"])):
                add(rec, f"{key}_false_alarm", field=item[0], expected=None, extracted=item[1])
        if not rec["actions"]["match"]:
            add(rec, "action", expected="one review flag" if rec["actions"]["expected_review"] else "no action", extracted=rec["actions"]["review_flag"])
    return log


# ---------------------------------------------------------------------------------------------------------------
# cross-check
# ---------------------------------------------------------------------------------------------------------------

def _fields_of(run: dict):
    return run["result"].fields


def score_pair(pair_folder: Path, as_of: date, budget=None, root: Path = FIXTURES) -> dict:
    truth = _truth(pair_folder)
    rr_run = run_document(root / "rent_roll" / truth["rent_roll"], as_of, budget)
    t_run = run_document(root / "t12" / truth["t12"], as_of, budget)
    report = crosscheck_mod.cross_check(0, 1, _fields_of(rr_run), _fields_of(t_run), truth.get("tolerance", 0.05), as_of)
    got = sorted(f["flag"] for f in report["flags"])
    comparisons = {c["key"]: c for c in report["comparisons"]}
    rent = comparisons["rent_income"]
    return {
        "id": truth["id"], "kind": "pair", "expected_flags": sorted(truth["expected_flags"]), "got_flags": got,
        "match": got == sorted(truth["expected_flags"]), "status": report["status"],
        "rent_income": {"status": rent["status"], "difference": rent.get("difference"), "difference_share": rent.get("difference_share"),
                        "expected_difference": truth.get("difference"), "expected_share": truth.get("difference_share"),
                        "difference_ok": rent.get("difference") is not None and truth.get("difference") is not None
                        and abs(rent["difference"] - truth["difference"]) <= MONEY_TOL},
        "property": comparisons["property_identity"]["status"], "vacancy": comparisons["vacancy_sanity"]["status"],
        "unavailable": report["unavailable"],
    }


def score_negative(pair_folder: Path, other_t12: str, as_of: date, budget=None, root: Path = FIXTURES) -> dict:
    """A rent roll against the T-12 of a different property: the identity check must say so."""
    truth = _truth(pair_folder)
    rr_run = run_document(root / "rent_roll" / truth["rent_roll"], as_of, budget)
    t_run = run_document(root / "t12" / other_t12, as_of, budget)
    report = crosscheck_mod.cross_check(0, 1, _fields_of(rr_run), _fields_of(t_run), truth.get("tolerance", 0.05), as_of)
    prop = next(c for c in report["comparisons"] if c["key"] == "property_identity")
    return {"id": f"{truth['id']}_vs_{other_t12}", "kind": "negative", "expected_flags": ["property_mismatch"],
            "got_flags": sorted(f["flag"] for f in report["flags"]), "match": prop["flag"] == "property_mismatch", "status": report["status"],
            "property": prop["status"]}


def run_crosscheck(as_of: date, budget=None, root: Path = FIXTURES, limit: int | None = None) -> list[dict]:
    pairs = sorted(p for p in (root / "pairs").glob("*/") if (p / "expected.json").exists())
    pairs = pairs[:limit] if limit else pairs
    records = [score_pair(p, as_of, budget, root) for p in pairs]
    if len(pairs) > 1:  # negative controls: each rent roll against the next pair's T-12 (a different property)
        for i, p in enumerate(pairs):
            records.append(score_negative(p, _truth(pairs[(i + 1) % len(pairs)])["t12"], as_of, budget, root))
    return records


def summarize_crosscheck(records: list[dict]) -> dict:
    rate = metrics.rate
    pairs = [r for r in records if r["kind"] == "pair"]
    negatives = [r for r in records if r["kind"] == "negative"]
    planted = [r for r in pairs if "rent_income_mismatch" in r["expected_flags"]]
    clean = [r for r in pairs if "rent_income_mismatch" not in r["expected_flags"]]
    return {
        "n_pairs": len(pairs), "n_negatives": len(negatives),
        "pair_flags_exact": rate(sum(r["match"] for r in pairs), len(pairs)),
        "rent_income_detection": rate(sum("rent_income_mismatch" in r["got_flags"] for r in planted), len(planted)),
        "rent_income_false_alarms": sum("rent_income_mismatch" in r["got_flags"] for r in clean),
        "rent_income_difference_ok": rate(sum(r["rent_income"]["difference_ok"] for r in pairs), len(pairs)),
        "property_same_recognised": rate(sum(r["property"] == "ok" for r in pairs), len(pairs)),
        "property_mismatch_detection": rate(sum(r["match"] for r in negatives), len(negatives)),
        "clean_pairs_passed": rate(sum(r["status"] == "ok" for r in clean), len(clean)),
        "detection_correct": rate(sum(r["match"] for r in records), len(records)),
    }


def failures_crosscheck(records: list[dict]) -> list[dict]:
    return [{"suite": "crosscheck", "fixture": r["id"], "doc_type": "pair", "variant": r["kind"], "kind": "flags",
             "expected": r["expected_flags"], "extracted": r["got_flags"], "flags": r["got_flags"]} for r in records if not r["match"]]
