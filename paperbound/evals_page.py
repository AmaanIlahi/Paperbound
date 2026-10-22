"""The /evals page: the newest saved result of each fixture from evals/results/, as one readable table.

Results are written by scripts/eval_fixtures.py. Nothing here calls a model or touches the database.
"""

import json
import re
from html import escape

from paperbound import config

RESULTS_DIR = config.ROOT / "evals" / "results"
_STAMP = re.compile(r"_\d{8}_\d{6}$")
COLUMNS = [
    ("pdf", "Fixture"), ("doc_type", "Type"), ("as_of", "As of"), ("classification_match", "Classified right"),
    ("fields_matched", "Fields matched"), ("obligation_match", "Obligation right"), ("brief_match", "Brief right"),
    ("actions_match", "Actions right"), ("openai_calls", "Model calls"), ("tokens", "Tokens"), ("cost_usd", "Cost (USD)"),
    ("latency_ms", "Latency (ms)"),
]


def latest_results(directory=None) -> list[dict]:
    """The summary of the newest result file per fixture, sorted by fixture name."""
    newest: dict[str, tuple[str, dict]] = {}
    for path in sorted((directory or RESULTS_DIR).glob("*.json")):
        try:
            summary = json.loads(path.read_text(encoding="utf-8")).get("summary")
        except (OSError, ValueError):
            continue
        if isinstance(summary, dict):
            newest[_STAMP.sub("", path.stem)] = (path.name, summary)  # sorted order: later timestamps win
    return [{**summary, "_file": name} for _, (name, summary) in sorted(newest.items())]


def _cell(key: str, value) -> str:
    if isinstance(value, bool):
        return '<span class="chip ok">yes</span>' if value else '<span class="chip bad">no</span>'
    if key == "cost_usd" and isinstance(value, (int, float)):
        return f"{value:.5f}"
    return escape("" if value is None else str(value))


def render(directory=None) -> str:
    rows = latest_results(directory)
    head = "".join(f"<th>{escape(label)}</th>" for _, label in COLUMNS)
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell(key, row.get(key))}</td>" for key, _ in COLUMNS) + "</tr>" for row in rows
    ) or f'<tr><td colspan="{len(COLUMNS)}">No saved results yet. Run scripts/eval_fixtures.py.</td></tr>'
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
<p class="muted">The newest saved run of each fixture, scored against hand-written expectations by <code>scripts/eval_fixtures.py</code>.
Every field, action and step of every run is kept in <code>evals/results/</code>.</p>
<div class="table-wrap"><table class="evals-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
</main></body></html>"""
