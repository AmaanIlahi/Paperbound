"""Aggregate per-fixture records into metrics, failure logs, a flat metric map for baselines, and markdown."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from paperbound import config
from paperbound.evals import metrics

RESULTS_DIR = config.ROOT / "evals" / "results"
BASELINE = config.ROOT / "evals" / "baseline.json"
LATEST_MD = RESULTS_DIR / "latest.md"
SYNTHETIC_RATES = (
    "classification_accuracy", "field_accuracy", "field_accuracy_expected_present", "missing_field_correct", "usable_rate",
    "grounding_rate", "page_accuracy", "obligation_correct", "brief_line_correct", "brief_doc_correct",
    "action_correct", "injection_model_resisted", "injection_system_held",
)
CUAD_RATES = ("f1", "precision", "recall", "date_exact", "grounding_rate", "detection_rate")


def _ratio(hits: int, total: int):
    return metrics.rate(hits, total)


# ---------------------------------------------------------------------------------------------------------------
# synthetic
# ---------------------------------------------------------------------------------------------------------------

def group_metrics(records: list[dict]) -> dict:
    fields = [r for rec in records for r in rec["fields"] if r["key"] != "policy_count"]
    present = [r for r in fields if r["expect_present"]]
    missing = [r for r in fields if not r["expect_present"]]
    extracted = [r for r in fields if r["extracted"] is not None]
    grounded = [r for r in extracted if r["grounded"]]
    paged = [r for r in fields if r["page_ok"] is not None]
    brief_lines = [ln for rec in records for ln in rec["brief"]["lines"]]
    brief_extra = sum(len(rec["brief"]["unexpected"]) for rec in records)
    injections = [rec["injection"] for rec in records if rec["injection"]]
    latencies = [rec["latency_ms"] for rec in records]
    out = {
        "n_docs": len(records),
        "n_fields": len(fields),
        "classification_accuracy": _ratio(sum(r["classification"]["match"] for r in records), len(records)),
        "field_accuracy": _ratio(sum(r["match"] for r in fields), len(fields)),
        "field_accuracy_expected_present": _ratio(sum(r["match"] for r in present), len(present)),
        "missing_field_correct": _ratio(sum(r["match"] for r in missing), len(missing)),
        "n_expected_missing": len(missing),
        "usable_rate": _ratio(sum(r["usable"] for r in present), len(present)),
        "grounding_rate": _ratio(len(grounded), len(extracted)),
        "page_accuracy": _ratio(sum(bool(r["page_ok"]) for r in paged), len(paged)),
        "calibration": metrics.calibration([(r["confidence"], r["match"]) for r in extracted if r["confidence"] is not None]),
        "obligation_correct": _ratio(sum(r["obligation"]["match"] for r in records), len(records)),
        "brief_line_correct": _ratio(sum(ln["status"] == "match" for ln in brief_lines), len(brief_lines) + brief_extra),
        "brief_line_wrong_rate": _ratio(sum(ln["status"] == "wrong" for ln in brief_lines) + brief_extra, len(brief_lines) + brief_extra),
        "brief_doc_correct": _ratio(sum(r["brief"]["match"] for r in records), len(records)),
        "action_correct": _ratio(sum(r["actions"]["match"] for r in records), len(records)),
        "action_unsafe_rate": _ratio(sum(r["actions"]["unsafe"] for r in records), len(records)),
        "cost_usd_mean": metrics.mean([r["cost_usd"] for r in records]),
        "cost_usd_total": sum(r["cost_usd"] for r in records),
        "tokens_mean": metrics.mean([r["tokens"] for r in records]),
        "latency_ms_mean": metrics.mean(latencies),
        "latency_ms_p95": metrics.p95(latencies),
        "calls_total": sum(r["calls"] or 0 for r in records),
    }
    if injections:
        out["injection_n"] = len(injections)
        out["injection_model_resisted"] = _ratio(sum(i["model_resisted"] for i in injections), len(injections))
        out["injection_system_held"] = _ratio(sum(i["held"] for i in injections), len(injections))
        out["injection_extracted_flagged"] = sum(i["flagged"] for i in injections)
        out["injection_unflagged_breach"] = sum(i["unflagged_breach"] for i in injections)
        out["injection_reached_actions"] = sum(i["reached_actions"] for i in injections)
    return out


def summarize_synthetic(records: list[dict]) -> dict:
    groups = {}
    for doc_type in sorted({r["doc_type"] for r in records}):
        of_type = [r for r in records if r["doc_type"] == doc_type]
        groups[f"{doc_type}/all"] = group_metrics(of_type)
        for variant in sorted({r["variant"] for r in of_type}):
            groups[f"{doc_type}/{variant}"] = group_metrics([r for r in of_type if r["variant"] == variant])
    return groups


def failures_synthetic(records: list[dict]) -> list[dict]:
    log = []

    def add(rec, kind, **kw):
        log.append({"suite": "synthetic", "fixture": rec["id"], "doc_type": rec["doc_type"], "variant": rec["variant"],
                    "template": rec["template"], "kind": kind, **kw})

    for rec in records:
        if rec["status"] != "done":
            add(rec, "pipeline_error", expected="status done", extracted=rec["status"], quote=None, flags=[], confidence=None,
                detail=rec["errors"])
        if not rec["classification"]["match"]:
            add(rec, "classification", expected=rec["classification"]["expected"], extracted=rec["classification"]["got"],
                quote=None, flags=[], confidence=None)
        for r in rec["fields"]:
            if not r["match"]:
                kind = "hallucinated_field" if not r["expect_present"] else ("missed_field" if r["extracted"] is None else "wrong_field")
                add(rec, kind, field=r["key"], expected=r["expected"], extracted=r["extracted"], quote=r["quote"], flags=r["flags"],
                    confidence=r["confidence"], page=r["page"])
        if not rec["obligation"]["match"]:
            add(rec, "obligation", expected=rec["obligation"]["expected"], extracted=rec["obligation"]["got"], quote=None, flags=[], confidence=None)
        for ln in rec["brief"]["lines"]:
            if ln["status"] != "match":
                add(rec, f"brief_{ln['status']}", field=ln["line"], expected=ln["expected"], extracted=ln["got"], quote=None, flags=[], confidence=None)
        for ln in rec["brief"]["unexpected"]:
            add(rec, "brief_unexpected", field=ln["line"], expected=None, extracted=ln["got"], quote=None, flags=[], confidence=None)
        a = rec["actions"]
        if not a["match"]:
            add(rec, "action", expected="types, numbers from the brief, nothing from blocked fields",
                extracted={k: a[k] for k in ("counts", "extra_tools", "types_ok", "numbers_ok", "blocked_ok", "stray_numbers", "stray_dates", "blocked_sources")},
                quote=None, flags=[], confidence=None)
        inj = rec["injection"]
        if inj and not inj["model_resisted"]:
            add(rec, "injection_followed", field=inj["field"], expected="true value", extracted=inj["extracted"], quote=inj["quote"],
                flags=inj["flags"], confidence=None, detail={"flagged": inj["flagged"], "reached_actions": inj["reached_actions"]})
    return log


def failure_modes(log: list[dict], top: int = 8) -> list[dict]:
    """Group failures by kind and field (injection and brief/action by kind) and rank by count."""
    counts: Counter = Counter()
    examples: dict = defaultdict(list)
    for item in log:
        base = (item.get("field") or "").rsplit(".", 1)[-1]
        mode = f"{item['kind']}: {base}" if base else item["kind"]
        counts[mode] += 1
        if len(examples[mode]) < 3:
            examples[mode].append(item["fixture"] if "fixture" in item else item.get("title"))
    return [{"mode": m, "count": c, "examples": examples[m]} for m, c in counts.most_common(top)]


# ---------------------------------------------------------------------------------------------------------------
# CUAD
# ---------------------------------------------------------------------------------------------------------------

def summarize_cuad(rows: list[dict], records: list[dict]) -> dict:
    out = {}
    cats = sorted({r["category"] for r in rows})
    for cat in [*cats, "all"]:
        of = [r for r in rows if cat == "all" or r["category"] == cat]
        labeled = [r for r in of if r["labeled"]]
        unlabeled = [r for r in of if not r["labeled"]]
        exact = [r for r in of if r["date_exact"] is not None]
        extracted = [r for r in of if r["extracted"]]
        out[cat] = {
            "n_contracts": len(of), "n_labeled": len(labeled), "n_unlabeled": len(unlabeled),
            "precision": metrics.mean([r["precision"] for r in labeled]), "recall": metrics.mean([r["recall"] for r in labeled]),
            "f1": metrics.mean([r["f1"] for r in labeled]),
            "detection_rate": _ratio(sum(r["extracted"] for r in labeled), len(labeled)),
            "false_positive_rate": _ratio(sum(r["extracted"] for r in unlabeled), len(unlabeled)),
            "n_date_exact": len(exact), "date_exact": _ratio(sum(bool(r["date_exact"]) for r in exact), len(exact)) if exact else None,
            "grounding_rate": _ratio(sum(r["grounded"] for r in extracted), len(extracted)),
            "calibration": metrics.calibration([(r["confidence"], (r["f1"] or 0) >= 0.5) for r in labeled if r["extracted"] and r["confidence"] is not None]),
        }
    out["all"] |= {
        "n_docs": len(records), "cost_usd_mean": metrics.mean([r["cost_usd"] for r in records]),
        "cost_usd_total": sum(r["cost_usd"] for r in records), "tokens_mean": metrics.mean([r["tokens"] for r in records]),
        "latency_ms_mean": metrics.mean([r["latency_ms"] for r in records]), "latency_ms_p95": metrics.p95([r["latency_ms"] for r in records]),
        "calls_total": sum(r["calls"] or 0 for r in records),
    }
    return out


def failures_cuad(rows: list[dict], f1_floor: float = 0.5) -> list[dict]:
    log = []
    for r in rows:
        base = {"suite": "cuad", "fixture": r["title"], "variant": "real", "field": r["category"], "quote": r["quote"],
                "flags": r["flags"], "confidence": r["confidence"], "extracted": r["value"]}
        if r["labeled"] and not r["extracted"]:
            log.append({**base, "kind": "missed_span", "expected": r["gold"]})
        elif r["labeled"] and (r["f1"] or 0) < f1_floor:
            log.append({**base, "kind": "low_overlap", "expected": r["gold"], "detail": {"f1": round(r["f1"] or 0, 3)}})
        elif not r["labeled"] and r["extracted"]:
            log.append({**base, "kind": "unlabeled_extraction", "expected": None})
        if r["date_exact"] is False and r["extracted"]:
            log.append({**base, "kind": "date_mismatch", "expected": r["gold"]})
    return log


# ---------------------------------------------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------------------------------------------

def flat_metrics(suite: str, groups: dict) -> dict[str, float]:
    """Higher-is-better rates keyed 'suite/group/metric', for the baseline diff."""
    rates = SYNTHETIC_RATES if suite == "synthetic" else CUAD_RATES
    flat = {}
    for group, values in groups.items():
        for name in rates:
            if values.get(name) is not None:
                flat[f"{suite}/{group}/{name}"] = round(values[name], 6)
    return flat


def load_baseline(path: Path = BASELINE) -> dict[str, float]:
    return json.loads(path.read_text(encoding="utf-8")).get("metrics", {}) if path.exists() else {}


def save_baseline(flat: dict[str, float], extra: dict, path: Path = BASELINE) -> None:
    existing = load_baseline(path)
    existing.update(flat)
    path.write_text(json.dumps({**extra, "metrics": dict(sorted(existing.items()))}, indent=1) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------------------------------------------
# reading saved runs and markdown
# ---------------------------------------------------------------------------------------------------------------

def latest_runs(directory: Path | None = None) -> dict[str, dict]:
    """The newest saved result per suite (synthetic_*.json, cuad_*.json)."""
    out = {}
    for suite in ("synthetic", "cuad"):
        files = sorted((directory or RESULTS_DIR).glob(f"{suite}_[0-9]*.json"))
        for path in reversed(files):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("suite") == suite:
                out[suite] = {**data, "_file": path.name}
                break
    return out


def pct(x, digits: int = 1) -> str:
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def _calibration_table(cal: dict) -> list[str]:
    lines = ["| Confidence | Fields | Accuracy | Mean confidence |", "|---|---|---|---|"]
    for bucket in metrics.BUCKETS:
        row = cal["buckets"][bucket]
        mc = "-" if row["mean_confidence"] is None else f"{row['mean_confidence']:.2f}"
        lines.append(f"| {bucket} | {row['n']} | {pct(row['accuracy'])} | {mc} |")
    lines.append(f"\nECE: {'-' if cal['ece'] is None else format(cal['ece'], '.3f')} over {cal['n']} fields.")
    return lines


def render_markdown(runs: dict[str, dict], drops: dict[str, list[dict]] | None = None) -> str:
    md = ["# Paperbound evals", "",
          "The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). "
          "CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of "
          "production accuracy.", ""]
    for suite, run in runs.items():
        md += [f"## {suite} (run {run['timestamp']}, as of {run['as_of']}, {run['n_docs']} documents)", ""]
        if run.get("aborted"):
            md += [f"**Aborted:** {run['aborted']}", ""]
        groups = run["groups"]
        if suite == "synthetic":
            md += ["| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |",
                   "|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for name, g in groups.items():
                md.append(f"| {name} | {g['n_docs']} | {pct(g['classification_accuracy'])} | {pct(g['field_accuracy'])} | "
                          f"{pct(g['missing_field_correct']) if g['n_expected_missing'] else '-'} | {pct(g['grounding_rate'])} | "
                          f"{pct(g['page_accuracy'])} | {pct(g['obligation_correct'])} | {pct(g['brief_line_correct'])} | "
                          f"{pct(g['action_correct'])} | ${g['cost_usd_mean']:.4f} | {g['latency_ms_p95'] / 1000:.1f} s |")
            inj = [(n, g) for n, g in groups.items() if n.endswith("/injection")]
            if inj:
                md += ["", "### Injection", "", "| Type | Attacks | Model resisted | System held | Flagged | Unflagged breach | Reached actions |",
                       "|---|---|---|---|---|---|---|"]
                for n, g in inj:
                    md.append(f"| {n.split('/')[0]} | {g['injection_n']} | {pct(g['injection_model_resisted'])} | {pct(g['injection_system_held'])} | "
                              f"{g['injection_extracted_flagged']} | {g['injection_unflagged_breach']} | {g['injection_reached_actions']} |")
            md += ["", "### Calibration (all variants)", ""]
            for doc_type in sorted({n.split("/")[0] for n in groups}):
                md += [f"**{doc_type}**", ""] + _calibration_table(groups[f"{doc_type}/all"]["calibration"]) + [""]
        else:
            md += ["| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |",
                   "|---|---|---|---|---|---|---|---|---|"]
            for name, g in groups.items():
                exact = "-" if g["date_exact"] is None else f"{pct(g['date_exact'])} ({g['n_date_exact']})"
                md.append(f"| {name} | {g['n_labeled']}/{g['n_contracts']} | {pct(g['precision'])} | {pct(g['recall'])} | {pct(g['f1'])} | "
                          f"{pct(g['detection_rate'])} | {pct(g['false_positive_rate'])} | {exact} | {pct(g['grounding_rate'])} |")
            a = groups["all"]
            md += ["", f"Cost per contract ${a['cost_usd_mean']:.4f}, {a['tokens_mean']:.0f} tokens, p95 latency {a['latency_ms_p95'] / 1000:.1f} s."]
        md += ["", "### Top failure modes", ""]
        md += [f"- {m['mode']}: {m['count']}" for m in run["failure_modes"]] or ["- none"]
        md += ["", f"Failure log: `evals/results/{run['failure_log']}`", ""]
        for d in (drops or {}).get(suite, []):
            md.append(f"- DROP {d['metric']}: {pct(d['baseline'])} -> {pct(d['current'])} ({d['delta_points']} points)")
        md.append("")
    return "\n".join(md) + "\n"
