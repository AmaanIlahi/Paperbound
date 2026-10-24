"""JSON shapes shared by the REST endpoints and the run event stream, so the page renders both the same way."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from paperbound.agent.tools import get_tool
from paperbound.db.models import ActionResult, Field, Obligation, ProposedAction, Step


def boxes_of(field: Field) -> list:
    return field.bboxes or ([field.bbox] if field.bbox else [])


def source_json(field: Field) -> dict:
    return {"field_id": field.id, "name": field.name, "page_no": field.page_no, "bboxes": boxes_of(field)}


def field_json(f: Field) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "value": f.value_json,
        "source_quote": f.source_quote,
        "page_no": f.page_no,
        "bbox": f.bbox,
        "bboxes": boxes_of(f),
        "confidence": f.confidence,
        "flags": f.flags,
        "label": f.label,
    }


def step_json(s: Step) -> dict:
    return {
        "seq": s.seq,
        "stage": s.stage,
        "status": s.status,
        "summary": s.output_summary,
        "input_summary": s.input_summary,
        "reason": s.reason,
        "error": s.error,
        "model": s.model,
        "tokens": s.tokens,
        "cost_usd": s.cost_usd,
        "duration_ms": s.duration_ms,
        "ts_offset_ms": s.ts_offset_ms,
    }


def obligation_json(o: Obligation) -> dict:
    return {
        "id": o.id,
        "kind": o.kind,
        "due_date": o.due_date,
        "description": o.description,
        "source_field_ids": o.source_field_ids,
    }


def brief_json(brief, by_name: dict[str, Field]) -> dict:
    """A pipeline Brief, with each line's sources resolved to stored fields."""
    return {
        "lines": [
            {
                "key": ln.key,
                "label": ln.label,
                "value": ln.value,
                "text": ln.text,
                "note": ln.note,
                "sources": [source_json(by_name[n]) for n in ln.source_fields if n in by_name],
            }
            for ln in brief.lines
        ],
        "skipped": brief.skipped,
    }


def action_json(session: Session, action: ProposedAction, fields: dict[int, Field]) -> dict:
    tool = get_tool(action.tool)
    result = session.scalar(
        select(ActionResult).where(ActionResult.action_id == action.id).order_by(ActionResult.id.desc())
    )
    return {
        "id": action.id,
        "run_id": action.run_id,
        "tool": action.tool,
        "side_effect": bool(tool and tool.side_effect),
        "args": action.args_json,
        "status": action.status,
        "rationale": action.rationale,
        "policy_reason": action.policy_reason,
        "group_key": action.group_key,
        "group_mode": action.group_mode,
        "sources": [source_json(fields[fid]) for fid in action.source_field_ids or [] if fid in fields],
        "result": (
            {"adapter": result.adapter, "response": result.response_json, "executed_at": result.executed_at}
            if result
            else None
        ),
    }
