"""The /evals page: the newest synthetic and CUAD runs (paperbound/evals/run.py), then the newest result of each demo
fixture (scripts/eval_fixtures.py), all read from evals/results/. Nothing here calls a model or touches the database.
"""

import json
import re
from html import escape

from paperbound import config
from paperbound.evals import metrics, report

RESULTS_DIR = config.ROOT / "evals" / "results"
_STAMP = re.compile(r"_\d{8}_\d{6}$")
COLUMNS = [
    ("pdf", "Fixture"), ("doc_type", "Type"), ("as_of", "As of"), ("classification_match", "Classified right"),
    ("fields_matched", "Fields matched"), ("obligation_match", "Obligation right"), ("brief_match", "Brief right"),
    ("actions_match", "Actions right"), ("openai_calls", "Model calls"), ("tokens", "Tokens"), ("cost_usd", "Cost (USD)"),
    ("latency_ms", "Latency (ms)"),
]


def latest_results(directory=None) -> list[dict]:
    """The summary of the newest result file per demo fixture, sorted by fixture name."""
    newest: dict[str, tuple[str, dict]] = {}
    for path in sorted((directory or RESULTS_DIR).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summary = data.get("summary") if isinstance(data, dict) else None  # suite runs and failure logs have none
        if isinstance(summary, dict):
            newest[_STAMP.sub("", path.stem)] = (path.name, summary)  # sorted order: later timestamps win
    return [{**summary, "_file": name} for _, (name, summary) in sorted(newest.items())]


def _cell(key: str, value) -> str:
    if isinstance(value, bool):
        return '<span class="chip ok">yes</span>' if value else '<span class="chip bad">no</span>'
    if key == "cost_usd" and isinstance(value, (int, float)):
        return f"{value:.5f}"
    return escape("" if value is None else str(value))


def _pct(x, digits: int = 1) -> str:
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{escape(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table class="evals-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def _runs(run: dict) -> str:
    runs = run.get("runs") or {}
    return ", ".join(f"{t} {ts}" for t, ts in runs.items()) or run["timestamp"]


def _synthetic_html(run: dict) -> str:
    groups = run["groups"]
    head = ["Type", "Variant", "Docs", "Classified", "Fields", "Missing handled", "Grounded", "Page", "Obligation", "Brief lines",
            "Actions", "Cost/doc", "Latency mean", "Latency p95"]
    rows = []
    for name, g in groups.items():
        doc_type, variant = name.split("/")
        rows.append([
            escape(doc_type), escape(variant), str(g["n_docs"]), _pct(g["classification_accuracy"]), _pct(g["field_accuracy"]),
            _pct(g["missing_field_correct"]) if g["n_expected_missing"] else "-", _pct(g["grounding_rate"]),
            _pct(g["page_accuracy"]), _pct(g["obligation_correct"]), _pct(g["brief_line_correct"]), _pct(g["action_correct"]),
            f"${g['cost_usd_mean']:.4f}", f"{g['latency_ms_mean'] / 1000:.1f} s", f"{g['latency_ms_p95'] / 1000:.1f} s",
        ])
    out = ["<h3>Summary</h3>", _table(head, rows)]

    cal_rows = []
    for doc_type in sorted({n.split("/")[0] for n in groups}):
        cal = groups[f"{doc_type}/all"]["calibration"]
        for bucket in metrics.BUCKETS:
            b = cal["buckets"][bucket]
            mc = "-" if b["mean_confidence"] is None else f"{b['mean_confidence']:.2f}"
            ece = "" if bucket != metrics.BUCKETS[0] else ("-" if cal["ece"] is None else f"{cal['ece']:.3f}")
            cal_rows.append([escape(doc_type), escape(bucket), str(b["n"]), _pct(b["accuracy"]), mc, ece])
    out += [
        "<h3>Calibration</h3>",
        '<p class="muted">Confidence is computed by code (grounding, validators, page quality), never by the model. '
        "ECE is the gap between confidence and accuracy, weighted by bucket size; 0 is perfectly calibrated.</p>",
        _table(["Type", "Confidence", "Fields", "Accuracy", "Mean confidence", "ECE"], cal_rows),
    ]

    inj_rows = [
        [escape(n.split("/")[0]), str(g["injection_n"]), _pct(g["injection_model_resisted"]), _pct(g["injection_system_held"]),
         str(g["injection_extracted_flagged"]), str(g["injection_unflagged_breach"]), str(g["injection_reached_actions"])]
        for n, g in groups.items() if n.endswith("/injection")
    ]
    if inj_rows:
        out += [
            "<h3>Prompt injection</h3>",
            '<p class="muted">Each injected document carries a poisoned value (instruction-style or clause-style). '
            '"Model resisted" means the poisoned value was not extracted. "System held" also counts a poisoned value that '
            "validators flagged and kept out of every action. A breach is a poisoned value extracted with no flag.</p>",
            _table(["Type", "Attacks", "Model resisted", "System held", "Extracted but flagged", "Unflagged breach", "Reached actions"], inj_rows),
        ]
    return "".join(out)


def _generic_html(run: dict) -> str:
    g = run["groups"]["generic/all"]
    head = ["Docs", "Routed generic", "Field accuracy", "Field precision", "Parties", "Dates", "Amounts", "Obligations", "Grounded",
            "Page", "Obligation set", "Actions", "Confidence cap held", "Cost/doc"]
    row = [str(g["n_docs"]), _pct(g["classification_accuracy"]), _pct(g["field_accuracy"]), _pct(g["field_precision"]),
           _pct(g["party_accuracy"]), _pct(g["date_accuracy"]), _pct(g["amount_accuracy"]), _pct(g["obligation_accuracy"]),
           _pct(g["grounding_rate"]), _pct(g["page_accuracy"]), _pct(g["obligation_correct"]), _pct(g["action_correct"]),
           _pct(g["confidence_cap_respected"]), f"${g['cost_usd_mean']:.4f}"]
    return ("<h3>Summary</h3>" + _table(head, [row])
            + f'<p class="muted">Field accuracy is the share of expected parties, dates, amounts and obligations found (by value). '
            f'Precision counts extra fields the document does not support against it ({g["n_extras"]} extra). '
            f'Email drafts proposed: {g["email_drafts"]}.</p>')


def _cuad_html(run: dict) -> str:
    groups = run["groups"]
    rows = []
    for name, g in groups.items():
        exact = "-" if g["date_exact"] is None else f"{_pct(g['date_exact'])} (n={g['n_date_exact']})"
        rows.append([
            escape(name.replace("_", " ")), f"{g['n_labeled']}/{g['n_contracts']}", _pct(g["precision"]), _pct(g["recall"]), _pct(g["f1"]),
            _pct(g["detection_rate"]), _pct(g["false_positive_rate"]), exact, _pct(g["grounding_rate"]),
        ])
    a = groups.get("all", {})
    cost = (f'<p class="muted">Per contract: ${a["cost_usd_mean"]:.4f}, {a["tokens_mean"]:.0f} tokens, '
            f'p95 latency {a["latency_ms_p95"] / 1000:.1f} s.</p>') if a.get("cost_usd_mean") is not None else ""
    head = ["Category", "Labeled", "Token precision", "Token recall", "Token F1", "Detected", "False positives", "Date exact match", "Grounded"]
    return "<h3>Summary</h3>" + _table(head, rows) + cost


def _modes_html(run: dict) -> str:
    items = "".join(f'<li>{escape(m["mode"])} <span class="muted">({m["count"]})</span></li>' for m in run["failure_modes"]) or "<li>none</li>"
    return f"<h3>Top failure modes</h3><ol>{items}</ol>"


def _suite_html(directory=None) -> str:
    runs = report.latest_runs(directory)
    if not runs:
        return '<p class="muted">No synthetic or CUAD run saved yet. Run <code>python -m paperbound.evals.run</code>.</p>'
    out = []
    if "synthetic" in runs:
        r = runs["synthetic"]
        n_fields = sum(g["n_fields"] for n, g in r["groups"].items() if n.endswith("/all"))
        out.append(
            '<section class="eval-suite"><h2>Synthetic fixtures</h2><p class="note"><b>Template-generated.</b> '
            f'{r["n_docs"]} documents ({n_fields} scored fields) rendered from seeded random values in 3 layouts per type, with '
            "distractors, missing fields, late pages and injected text. Easier and more regular than real documents, so read it as a "
            f'regression check, not as accuracy on your documents. Runs: {escape(_runs(r))}; as of {escape(r["as_of"])}.</p>'
            f"{_synthetic_html(r)}{_modes_html(r)}</section>"
        )
    if "generic" in runs:
        r = runs["generic"]
        out.append(
            '<section class="eval-suite"><h2>Generic fallback</h2><p class="note"><b>Documents of an unsupported type.</b> '
            f'{r["n_docs"]} hand-written notices, invoices and orders (including a capital call notice), run through the generic '
            "schema. Scored separately from the typed documents above: generic fields are capped in confidence and can only "
            f'produce reminders and a review flag. Run {escape(r["timestamp"])}; as of {escape(r["as_of"])}.</p>'
            f"{_generic_html(r)}{_modes_html(r)}</section>"
        )
    if "cuad" in runs:
        r = runs["cuad"]
        out.append(
            '<section class="eval-suite"><h2>CUAD (real contracts)</h2><p class="note"><b>Real contracts, span-based scoring.</b> '
            f'{r["n_docs"]} of a fixed {r.get("sample_size", r["n_docs"])}-contract sample (seed 42) from CUAD v1, extraction only. '
            "Scores are token overlap between our quote and the human-labeled span, so a correct answer worded differently can score "
            f'low. Run {escape(r["timestamp"])}.</p>{_cuad_html(r)}{_modes_html(r)}</section>'
        )
    return "".join(out)


def render(directory=None) -> str:
    rows = latest_results(directory)
    head = "".join(f"<th>{escape(label)}</th>" for _, label in COLUMNS)
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell(key, row.get(key))}</td>" for key, _ in COLUMNS) + "</tr>" for row in rows
    ) or f'<tr><td colspan="{len(COLUMNS)}">No saved results yet. Run scripts/eval_fixtures.py.</td></tr>'
    suites = _suite_html(directory)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evals · Paperbound</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/web/style.css">
</head><body class="page-evals">
<header class="topbar"><a class="brand" href="/">Paperbound</a>
<nav><a href="/#how">How it works</a><a href="/evals" aria-current="page">Evals</a>
<a href="https://github.com/AmaanIlahi/Paperbound">GitHub</a></nav></header>
<main class="evals">
<h1>Evals</h1>
<p class="muted">Three suites, all run locally against the same pipeline as the app, as of 2026-10-22. Every result and failure is kept in <code>evals/results/</code>.</p>
{suites}
<h2>Demo fixtures</h2>
<p class="muted">The newest saved run of each demo fixture, scored against hand-written expectations by <code>scripts/eval_fixtures.py</code>.</p>
<div class="table-wrap"><table class="evals-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
</main></body></html>"""
