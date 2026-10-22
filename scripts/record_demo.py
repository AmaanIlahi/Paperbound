"""Record a real pipeline run as a demo snapshot (architecture section 2).

Runs the live pipeline (real OpenAI calls, no database) on a fixture PDF as of AS_OF and exports
fixtures/demo/<fixture>/:
    source.pdf, pages/<n>.png, snapshot.json, narration.json

narration.json is drafted from each step's real data, then meant to be edited by hand and committed. An existing
narration.json is never overwritten unless you pass --redraft.

Usage: python scripts/record_demo.py [--only lease] [--as-of 2026-10-22] [--redraft]
Costs about 2 OpenAI calls per fixture.
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperbound.agent.tools import get_tool  # noqa: E402
from paperbound.pipeline.extract import MODEL  # noqa: E402
from paperbound.pipeline.parse import parse_pdf  # noqa: E402
from paperbound.pipeline.runner import make_parse_step, run_pipeline  # noqa: E402

DEMO = ROOT / "fixtures" / "demo"
DEFAULT_AS_OF = "2026-10-22"
FIXTURES = {
    "lease": ("Lease", "lease.pdf"),
    "insurance_certificate": ("Insurance Certificate", "insurance_certificate.pdf"),
}
STAGE_ORDER = ["parse", "classify", "extract", "validate", "obligations", "impact", "planner"]


def build_snapshot(name: str, label: str, filename: str, pages, result, as_of: date) -> dict:
    ids = {f.name: i for i, f in enumerate(result.fields, start=1)}
    by_name = {f.name: f for f in result.fields}

    def source(field_name: str) -> dict:
        f = by_name[field_name]
        return {"field_id": ids[field_name], "name": field_name, "page_no": f.page_no, "bboxes": f.bboxes}

    brief = result.brief
    plan = result.plan
    return {
        "fixture": name,
        "label": label,
        "filename": filename,
        "doc_type": result.doc_type,
        "classification": {
            "method": result.classification.method, "reason": result.classification.reason,
            "scores": result.classification.scores,
        } if result.classification else None,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "model": MODEL,
        "status": result.status,
        "message": result.message,
        "page_count": len(pages),
        "cost_usd": result.cost_usd,
        "tokens": result.tokens,
        "openai_calls": 1 + (plan.api_calls if plan else 0) + (1 if result.classification and result.classification.method == "llm" else 0),
        "pages": [
            {"page_no": p.page_no, "route": p.route, "quality_score": p.quality_score, "width_pt": p.width_pt,
             "height_pt": p.height_pt, "words": p.words}
            for p in pages
        ],
        "steps": [
            {"seq": i, "stage": s.stage, "status": s.status, "summary": s.output_summary, "input_summary": s.input_summary,
             "reason": s.reason, "error": s.error, "model": s.model, "tokens": s.tokens, "cost_usd": s.cost_usd,
             "duration_ms": s.duration_ms, "ts_offset_ms": s.ts_offset_ms}
            for i, s in enumerate(result.steps, start=1)
        ],
        "fields": [
            {"id": ids[f.name], "name": f.name, "value": f.value, "source_quote": f.source_quote, "page_no": f.page_no,
             "bbox": f.bbox, "bboxes": f.bboxes, "confidence": f.confidence, "flags": f.flags}
            for f in result.fields
        ],
        "obligations": [
            {"kind": o.kind, "due_date": o.due_date.isoformat(), "description": o.description,
             "source_field_ids": [ids[n] for n in o.source_fields if n in ids]}
            for o in result.obligations
        ],
        "brief": {
            "lines": [
                {"key": ln.key, "label": ln.label, "value": ln.value, "text": ln.text, "note": ln.note,
                 "sources": [source(n) for n in ln.source_fields if n in ids]}
                for ln in (brief.lines if brief else [])
            ],
            "skipped": brief.skipped if brief else [],
        },
        "actions": [
            {
                "id": i, "tool": a.tool, "side_effect": bool(get_tool(a.tool) and get_tool(a.tool).side_effect),
                # the run-id link placeholder becomes a demo link the page understands
                "args": json.loads(json.dumps(a.args).replace("/#run={{RUN_ID}}", f"/#demo={name}")),
                "status": "pending", "rationale": a.rationale, "policy_reason": a.policy_reason,
                "group_key": a.group_key, "group_mode": a.group_mode, "result": None,
                "sources": [source(n) for n in a.source_fields if n in ids],
            }
            for i, a in enumerate(plan.actions if plan else [], start=1)
        ],
    }


# ---------------------------------------------------------------------------------------------------------------
# narration: drafted from the recorded data, then hand-edited
# ---------------------------------------------------------------------------------------------------------------

def draft_narration(snap: dict) -> dict:
    steps = {s["stage"]: s for s in snap["steps"]}
    fields = snap["fields"]
    present = [f for f in fields if f["value"] is not None]
    grounded = [f for f in present if "ungrounded" not in f["flags"]]
    flagged = [f for f in fields if f["flags"]]
    pages = snap["page_count"]
    cls = snap["classification"] or {}
    kind = snap["label"].lower()
    brief = {ln["key"]: ln for ln in snap["brief"]["lines"]}
    n_actions = len(snap["actions"])
    groups = {a["group_key"] for a in snap["actions"] if a["group_key"]}

    text = {}
    text["parse"] = (
        f"The PDF has a text layer on all {pages} page(s), so no OCR was needed. This step is deterministic: PyMuPDF reads "
        "every word and its exact position straight from the file. No model is involved."
    )
    if cls.get("method") == "llm":
        text["classify"] = "The keyword and layout heuristics were not sure what this document is, so a small LLM call decided. " + cls.get("reason", "")
    else:
        scores = cls.get("scores", {})
        top = sorted(scores.values(), reverse=True)
        lead = f" (top score {top[0]:g}, runner-up {top[1]:g})" if len(top) > 1 else ""
        text["classify"] = (
            f"Keyword and layout heuristics recognised a {kind}{lead}, so no model was called. Code decides whenever it "
            "can: it is free and gives the same answer every time. An LLM is only the fallback when the heuristics are unsure."
        )
    text["extract"] = (
        f"This is the first place a model is used. gpt-4o-mini read the {pages} page(s) and returned {len(present)} fields, each "
        "with the exact quote it relied on and the page it found it on. Rules cannot read free-form clauses, so an LLM does. "
        f"Nothing it says is trusted yet ({steps['extract']['tokens']} tokens, ${steps['extract']['cost_usd']:.4f})."
    )
    flag_text = "No field was flagged." if not flagged else "Flagged: " + "; ".join(f"{f['name']} ({', '.join(f['flags'])})" for f in flagged) + "."
    text["validate"] = (
        f"Now code checks the model. {len(grounded)} of {len(present)} fields were found word for word on the page, and each "
        "got a box around its source (click a field to see it). Dates, amounts and ranges are checked, the value must appear "
        "in its quote, and a quote that reads like an instruction is blocked. Confidence is 0.6 x grounding + 0.3 x checks "
        f"+ 0.1 x page quality; the model's own confidence is never used. {flag_text}"
    )
    obs = snap["obligations"]
    first = obs[0]["description"] if obs else "none"
    text["obligations"] = (
        f"Code turned the checked fields into {len(obs)} obligation(s): {first}"
        + (" (and others below)." if len(obs) > 1 else ".")
        + " This is plain date arithmetic, with no model, and it only uses fields that passed validation."
    )
    shown = [f"{brief[k]['label']}: {brief[k]['text']}" for k in list(brief)[:4]]
    text["impact"] = (
        f"The decision brief, as of {snap['as_of']}: " + "; ".join(shown)
        + ". Every number is computed by code from fields that passed validation, and each one cites the fields it came "
        "from (click a number to see them). A line is skipped, with a reason, if its inputs are missing or blocked."
    )
    planner = steps.get("planner", {})
    text["planner"] = (
        f"The second model step. The LLM proposes {n_actions} action(s) with tool calls, but it only writes the wording: code "
        "supplies every number, date and name, chooses the reminder dates, and rejects any proposal whose numbers do not match "
        f"the brief. {planner.get('reason', '')}"
    ).strip()
    exclusive = "notice_choice" in groups
    text["gate"] = (
        "The approval gate is deterministic code, not the model: every action starts pending, an email is always only a draft "
        "(never sent), and the recipient stays a placeholder."
        + (" Approving one notice rejects the other, because they are one decision." if exclusive else "")
        + " Try approving below. In this demo, approvals run in a sandbox inside your browser: nothing is sent to a server."
    )
    return {
        "intro": (
            f"This is a recorded run of the real pipeline on a {kind} ({snap['filename']}, {pages} page(s)), replayed as of "
            f"{snap['as_of']}. No model is called while you watch, and nothing leaves your browser."
        ),
        "steps": text,
        "actions": {
            "reminders": "Staged reminders before the deadline. Code chose the dates and filled in every number; the model only wrote the intro line. Approve them together or one by one.",
            "notice_choice": "Two drafts of one decision: approving one rejects the other. Drafts only: nothing is sent, and the recipient stays a placeholder.",
            "tool:draft_email": "A draft email. Approving it only saves a draft in the sandbox inbox: it is never sent.",
            "tool:upsert_record": "A tracker record built entirely by code from the brief: no model wrote any of it.",
            "default": "A proposed action. It stays pending until you approve or reject it.",
        },
        "outro": (
            f"That was the whole run: {snap['openai_calls']} model call(s), {snap['tokens']} tokens, ${snap['cost_usd']:.4f}. Everything "
            "else was code. Approve or reject actions to see the sandbox panel."
        ),
    }


# ---------------------------------------------------------------------------------------------------------------

def record(name: str, as_of: date, redraft: bool) -> None:
    label, filename = FIXTURES[name]
    os.environ["AS_OF"] = as_of.isoformat()
    pdf = DEMO / filename
    data = pdf.read_bytes()
    started = time.perf_counter()
    parse_started = time.perf_counter()
    pages = parse_pdf(data)
    parse_ms = int((time.perf_counter() - parse_started) * 1000)
    result = run_pipeline(pages, make_parse_step(pages, parse_ms, reused=False), today=as_of)
    if result.status != "done":
        sys.exit(f"{name}: pipeline {result.status}: {[f'{s.stage}: {s.error}' for s in result.steps if s.error]}")
    snap = build_snapshot(name, label, filename, pages, result, as_of)

    out = DEMO / name
    (out / "pages").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, out / "source.pdf")
    for p in pages:
        (out / "pages" / f"{p.page_no}.png").write_bytes(p.image_png)
    (out / "snapshot.json").write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8", newline="\n")
    narration_path = out / "narration.json"
    if redraft or not narration_path.exists():
        narration_path.write_text(json.dumps(draft_narration(snap), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        note = "narration drafted"
    else:
        note = "existing narration.json kept"
    print(f"{name}: {len(snap['fields'])} fields, {len(snap['actions'])} actions, {snap['openai_calls']} OpenAI call(s), "
          f"${snap['cost_usd']:.5f}, {int((time.perf_counter() - started) * 1000)} ms; {note}; wrote {out.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=sorted(FIXTURES))
    parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    parser.add_argument("--redraft", action="store_true", help="overwrite an existing narration.json")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)
    for name in ([args.only] if args.only else list(FIXTURES)):
        record(name, as_of, args.redraft)


if __name__ == "__main__":
    main()
