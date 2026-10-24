"""Generic-fallback eval: run the pipeline on the generic fixtures (evals/fixtures/generic) and score every stage.

The documents are notices, invoices and orders: a type Paperbound has no schema for. Expectations come from
scripts/generate_fixtures.py, which prints every date and amount a document carries from the same specs, so recall
and precision both mean something. Scoring is by value (a date, an amount, a name), because the model chooses its
own labels; labels are scored separately as a softer check.
"""

import json
import re
import time
from datetime import date
from pathlib import Path

from paperbound import config
from paperbound.evals import metrics
from paperbound.pipeline.generic import unusable
from paperbound.pipeline.parse import parse_pdf
from paperbound.pipeline.runner import make_parse_step, run_pipeline
from paperbound.pipeline.validate import BLOCKING_FLAGS

FIXTURES = config.ROOT / "evals" / "fixtures" / "generic"
CATEGORIES = ("party", "date", "amount", "obligation")
_CURRENCY = re.compile(r"\(([A-Z]{3})\)$")


def discover(limit: int | None = None) -> list[Path]:
    folders = sorted(FIXTURES.glob("*/"))
    return folders[:limit] if limit else folders


def _has(text, words: list[str]) -> bool:
    text = str(text or "").casefold()
    return any(w.casefold() in text for w in words)


def _extracted(fields: list) -> dict[str, list]:
    """Present extracted values by category, in the order the model returned them."""
    out = {c: [] for c in CATEGORIES}
    for f in fields:
        if f.value is None:
            continue
        for category, suffix in (("party", ".name"), ("date", ".date"), ("amount", ".amount"), ("obligation", ".description")):
            if f.name.endswith(suffix):
                out[category].append(f)
    return out


def _row(category: str, key: str, expected, found, ok: bool, expected_pages: list[int], label_ok: bool | None, extra: dict | None = None) -> dict:
    blocked = bool(found and set(found.flags) & BLOCKING_FLAGS)
    return {
        "category": category, "key": key, "expected": expected, "extracted": found.value if found else None, "match": ok,
        "usable": ok and not blocked, "blocked": blocked, "label_ok": label_ok if ok else None, "name": found.name if found else None,
        "confidence": found.confidence if found else None, "grounded": bool(found and "ungrounded" not in found.flags),
        "page": found.page_no if found else None, "page_ok": (found.page_no in expected_pages) if (ok and found) else None,
        "flags": found.flags if found else [], "quote": found.source_quote if found else None, "label": found.label if found else None,
        **(extra or {}),
    }


def score_fields(expected: dict, result) -> tuple[list[dict], list[dict]]:
    """(one row per expected item, extracted items that match no expected item)."""
    got = _extracted(result.fields)
    by_name = {f.name: f for f in result.fields}
    rows, used = [], set()

    def take(category, test):
        for f in got[category]:
            if f.name not in used and test(f):
                used.add(f.name)
                return f
        return None

    for item in expected["parties"]:
        f = take("party", lambda f, n=item["name"]: metrics.fuzzy_match(n, f.value))
        rows.append(_row("party", item["name"], item["name"], f, f is not None, item["pages"], _has(f.label, item["role_any"]) if f else None))
    for item in expected["dates"]:
        f = take("date", lambda f, v=item["value"]: metrics.norm_date(f.value) == v)
        rows.append(_row("date", item["value"], item["value"], f, f is not None, item["pages"], _has(f.label, item["label_any"]) if f else None))
    for item in expected["amounts"]:
        def same(f, v=item["value"], c=item["currency"]):
            n = metrics.norm_number(f.value)
            return n is not None and abs(n - v) < 0.005 and (_CURRENCY.search(f.label or "") or [None, None])[1] == c
        f = take("amount", same)
        if f is None:  # right number, wrong or missing currency: still found, but not correct
            f_near = take("amount", lambda f, v=item["value"]: (metrics.norm_number(f.value) or -1) == v)
            rows.append(_row("amount", str(item["value"]), item["value"], f_near, False, item["pages"], None))
            continue
        rows.append(_row("amount", str(item["value"]), item["value"], f, True, item["pages"], _has(f.label, item["label_any"])))
    for item in expected["obligations"]:
        def same_ob(f, d=item["due_date"], k=item["keywords"]):
            due = by_name.get(f.name.replace(".description", ".due_date"))
            return due is not None and due.value == d and _has(f.value, k)
        f = take("obligation", same_ob)
        rows.append(_row("obligation", item["due_date"], item["due_date"], f, f is not None, item["pages"], None))
    extras = [
        {"category": c, "name": f.name, "value": f.value, "label": f.label, "quote": f.source_quote, "flags": f.flags}
        for c in CATEGORIES for f in got[c] if f.name not in used
    ]
    return rows, extras


def score_obligations(expected: dict, result) -> dict:
    want, got = sorted(expected["deadlines"]), sorted(o.due_date.isoformat() for o in result.obligations)
    return {"expected": want, "got": got, "match": want == got}


def score_actions(expected: dict, result, cap: float) -> dict:
    actions = result.plan.actions if result.plan else []
    allowed = set(expected["actions"]["tools_allowed"])
    tools = [a.tool for a in actions]
    reminder_dates = sorted(a.args.get("date") for a in actions if a.tool == "create_calendar_event")
    by_name = {f.name: f for f in result.fields}
    blocked = sorted({s for a in actions for s in a.source_fields if unusable(by_name.get(s))})
    tools_ok = set(tools) <= allowed and tools.count("flag_for_review") == 1
    dates_ok = reminder_dates == sorted(expected["actions"]["reminder_dates"])
    return {
        "proposed": [{"tool": a.tool, "args": a.args, "source_fields": a.source_fields} for a in actions],
        "tools": tools, "tools_ok": tools_ok, "dates_ok": dates_ok, "blocked_sources": blocked, "blocked_ok": not blocked,
        "no_email": "draft_email" not in tools, "reminder_dates": reminder_dates,
        "match": tools_ok and dates_ok and not blocked, "unsafe": not tools_ok or bool(blocked),
        "cap_ok": all(f.confidence <= cap + 1e-9 for f in result.fields),
        "max_confidence": max((f.confidence for f in result.fields), default=0.0),
    }


def run_fixture(folder: Path, as_of: date, budget=None) -> dict:
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    calls_before = budget.session_calls if budget else 0
    started = time.perf_counter()
    pages = parse_pdf((folder / expected["pdf"]).read_bytes())
    parse_ms = int((time.perf_counter() - started) * 1000)
    result = run_pipeline(pages, make_parse_step(pages, parse_ms, reused=False), today=as_of)
    latency_ms = int((time.perf_counter() - started) * 1000)

    rows, extras = score_fields(expected, result)
    cls = result.classification
    return {
        "id": expected["id"], "doc_type": "generic", "variant": "clean", "template": "generic", "status": result.status,
        "errors": [f"{s.stage}: {s.error}" for s in result.steps if s.error],
        "classification": {"expected": "generic", "got": result.doc_type, "method": cls.method if cls else None,
                           "match": result.doc_type == "generic", "type_label": result.type_label,
                           "label_ok": _has(result.type_label, expected["type_label_any"])},
        "fields": rows, "extras": extras, "obligation": score_obligations(expected, result),
        "actions": score_actions(expected, result, config.generic_confidence_cap()),
        "cost_usd": result.cost_usd, "tokens": result.tokens, "latency_ms": latency_ms,
        "calls": (budget.session_calls - calls_before) if budget else None,
    }


# ---- aggregation (the report module re-exports these) ----

def summarize(records: list[dict]) -> dict:
    fields = [r for rec in records for r in rec["fields"]]
    extras = [e for rec in records for e in rec["extras"]]
    extracted_total = sum(r["extracted"] is not None for r in fields) + len(extras)
    matched = [r for r in fields if r["match"]]
    paged = [r for r in fields if r["page_ok"] is not None]
    labelled = [r for r in matched if r["label_ok"] is not None]
    present = [r for r in fields if r["extracted"] is not None]
    latencies = [r["latency_ms"] for r in records]

    def rate(hits, total):
        return metrics.rate(hits, total)

    out = {
        "n_docs": len(records), "n_fields": len(fields), "n_extras": len(extras),
        "classification_accuracy": rate(sum(r["classification"]["match"] for r in records), len(records)),
        "type_label_ok": rate(sum(r["classification"]["label_ok"] for r in records), len(records)),
        "field_accuracy": rate(len(matched), len(fields)),
        "field_precision": rate(len(matched), len(matched) + len(extras)),
        "usable_rate": rate(sum(r["usable"] for r in fields), len(fields)),
        "grounding_rate": rate(sum(r["grounded"] for r in present) + sum("ungrounded" not in e["flags"] for e in extras), extracted_total),
        "page_accuracy": rate(sum(bool(r["page_ok"]) for r in paged), len(paged)),
        "label_accuracy": rate(sum(bool(r["label_ok"]) for r in labelled), len(labelled)),
        "obligation_correct": rate(sum(r["obligation"]["match"] for r in records), len(records)),
        "action_correct": rate(sum(r["actions"]["match"] for r in records), len(records)),
        "action_unsafe_rate": rate(sum(r["actions"]["unsafe"] for r in records), len(records)),
        "confidence_cap_respected": rate(sum(r["actions"]["cap_ok"] for r in records), len(records)),
        "email_drafts": sum(not r["actions"]["no_email"] for r in records),
        "cost_usd_mean": metrics.mean([r["cost_usd"] for r in records]),
        "cost_usd_total": sum(r["cost_usd"] for r in records),
        "tokens_mean": metrics.mean([r["tokens"] for r in records]),
        "latency_ms_mean": metrics.mean(latencies), "latency_ms_p95": metrics.p95(latencies),
        "calls_total": sum(r["calls"] or 0 for r in records),
        "calibration": metrics.calibration([(r["confidence"], r["match"]) for r in present if r["confidence"] is not None]),
    }
    for category in CATEGORIES:
        of = [r for r in fields if r["category"] == category]
        out[f"{category}_accuracy"] = rate(sum(r["match"] for r in of), len(of))
        out[f"n_{category}"] = len(of)
    return out


def failures(records: list[dict]) -> list[dict]:
    log = []

    def add(rec, kind, **kw):
        log.append({"suite": "generic", "fixture": rec["id"], "doc_type": "generic", "variant": "clean", "template": "generic", "kind": kind, **kw})

    for rec in records:
        if rec["status"] != "done":
            add(rec, "pipeline_error", expected="status done", extracted=rec["status"], quote=None, flags=[], confidence=None, detail=rec["errors"])
        if not rec["classification"]["match"]:
            add(rec, "classification", expected="generic", extracted=rec["classification"]["got"], quote=None, flags=[], confidence=None)
        for r in rec["fields"]:
            if not r["match"]:
                add(rec, "missed_field" if r["extracted"] is None else "wrong_field", field=r["category"],
                    expected=r["expected"], extracted=r["extracted"], quote=r["quote"], flags=r["flags"], confidence=r["confidence"], page=r["page"])
        for e in rec["extras"]:
            add(rec, "extra_field", field=e["category"], expected=None, extracted=e["value"], quote=e["quote"], flags=e["flags"], confidence=None)
        if not rec["obligation"]["match"]:
            add(rec, "obligation", expected=rec["obligation"]["expected"], extracted=rec["obligation"]["got"], quote=None, flags=[], confidence=None)
        a = rec["actions"]
        if not a["match"]:
            add(rec, "action", expected="reminders for future deadlines, one review flag, nothing else",
                extracted={k: a[k] for k in ("tools", "reminder_dates", "tools_ok", "dates_ok", "blocked_sources")}, quote=None, flags=[], confidence=None)
        if not a["cap_ok"]:
            add(rec, "confidence_cap", expected="<= cap", extracted=a["max_confidence"], quote=None, flags=[], confidence=a["max_confidence"])
    return log
