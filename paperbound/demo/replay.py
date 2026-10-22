"""Demo replay: streams a recorded run from fixtures/demo/<fixture>/ as server-sent events.

This module must stay free of the database and the OpenAI client: demo mode has to work with no DATABASE_URL, no
OPENAI_API_KEY, and a paused database. It reads local files only (tests assert that nothing else is imported).
"""

import asyncio
import json
import re
from pathlib import Path
from typing import AsyncIterator

from paperbound import config

STEP_CAP_SECONDS = 1.5  # a recorded step that took longer is replayed in this long
MIN_STEP_SECONDS = 0.3  # ...and one that took no time stays on screen long enough to read its narration
FIELD_GAP_SECONDS = 0.12  # between progressively revealed fields (validated boxes)
ACTION_GAP_SECONDS = 0.25
_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class FixtureNotFound(Exception):
    pass


def _dir(demo_dir: Path | None) -> Path:
    return demo_dir if demo_dir is not None else config.DEMO_DIR


def fixture_path(name: str, demo_dir: Path | None = None) -> Path:
    """Directory of one fixture. Names are restricted so a request can never leave the demo directory."""
    if not _NAME_RE.match(name):
        raise FixtureNotFound(name)
    path = _dir(demo_dir) / name
    if not (path / "snapshot.json").is_file():
        raise FixtureNotFound(name)
    return path


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot(name: str, demo_dir: Path | None = None) -> dict:
    path = fixture_path(name, demo_dir)
    snapshot = _read_json(path / "snapshot.json")
    narration_file = path / "narration.json"
    snapshot["narration"] = _read_json(narration_file) if narration_file.is_file() else {}
    return snapshot


def badge(snapshot: dict) -> str:
    return (
        f"Demo replay · recorded {snapshot['recorded_at'][:10]} · as of {snapshot['as_of']} · model {snapshot['model']}"
    )


def list_fixtures(demo_dir: Path | None = None) -> list[dict]:
    out = []
    root = _dir(demo_dir)
    for snap in sorted(root.glob("*/snapshot.json")) if root.is_dir() else []:
        try:
            data = _read_json(snap)
        except (OSError, ValueError):
            continue
        out.append(
            {
                "id": snap.parent.name,
                "label": data.get("label", snap.parent.name),
                "filename": data.get("filename"),
                "doc_type": data.get("doc_type"),
                "recorded_at": data.get("recorded_at"),
                "as_of": data.get("as_of"),
                "model": data.get("model"),
                "page_count": data.get("page_count"),
                "badge": badge(data),
            }
        )
    return out


def page_payload(snapshot: dict, fixture: str, page_no: int) -> dict | None:
    """Same shape as the live GET /documents/{id}/pages/{n}, with the image served from the snapshot."""
    page = next((p for p in snapshot["pages"] if p["page_no"] == page_no), None)
    if page is None:
        return None
    return {
        "document_id": None,
        "page_no": page["page_no"],
        "route": page["route"],
        "quality_score": page["quality_score"],
        "image_url": f"/demo/{fixture}/pages/{page_no}/image.png",
        "width_pt": page["width_pt"],
        "height_pt": page["height_pt"],
        "words": page["words"],
    }


def image_path(fixture: str, page_no: int, demo_dir: Path | None = None) -> Path | None:
    path = fixture_path(fixture, demo_dir) / "pages" / f"{int(page_no)}.png"
    return path if path.is_file() else None


def step_seconds(duration_ms: int) -> float:
    """How long a recorded step is replayed: its real duration, capped, with a floor so it can be read."""
    return max(MIN_STEP_SECONDS, min(duration_ms / 1000.0, STEP_CAP_SECONDS))


STEP_EVENT_KEYS = ("seq", "stage", "status", "summary", "input_summary", "reason", "error", "model", "tokens", "cost_usd",
                   "duration_ms", "ts_offset_ms")


def build_events(snapshot: dict) -> list[tuple[float, str, dict]]:
    """The whole replay as (seconds to wait before it, event name, data). Pure: no sleeping, no I/O."""
    narration = snapshot.get("narration", {})
    step_text = narration.get("steps", {})
    action_text = narration.get("actions", {})
    events: list[tuple[float, str, dict]] = [
        (
            0.0,
            "meta",
            {
                "fixture": snapshot["fixture"], "label": snapshot["label"], "filename": snapshot["filename"],
                "doc_type": snapshot["doc_type"], "classification": snapshot.get("classification"),
                "recorded_at": snapshot["recorded_at"], "as_of": snapshot["as_of"], "model": snapshot["model"],
                "page_count": snapshot["page_count"], "badge": badge(snapshot), "narration": narration.get("intro", ""),
            },
        )
    ]
    fields = snapshot["fields"]
    for step in snapshot["steps"]:
        stage = step["stage"]
        base = {k: step.get(k) for k in STEP_EVENT_KEYS}
        events.append((0.2, "step", {**base, "status": "running", "narration": ""}))
        budget = step_seconds(step.get("duration_ms") or 0)
        if stage == "extract":
            gap = budget / (len(fields) + 1)
            for f in fields:
                events.append((gap, "field", {"phase": "extracted", "name": f["name"], "value": f["value"],
                                              "page_no": f["page_no"], "source_quote": f["source_quote"]}))
            events.append((gap, "step", {**base, "narration": step_text.get(stage, "")}))
        elif stage == "validate":
            for f in fields:
                events.append((FIELD_GAP_SECONDS, "field", {"phase": "validated", **f}))
            events.append((max(budget - FIELD_GAP_SECONDS * len(fields), 0.1), "step", {**base, "narration": step_text.get(stage, "")}))
        else:
            events.append((budget, "step", {**base, "narration": step_text.get(stage, "")}))
        if stage == "obligations":
            events.append((0.1, "obligations", {"obligations": snapshot["obligations"]}))
        elif stage == "impact":
            events.append((0.1, "brief", snapshot["brief"]))
        elif stage == "planner":
            for action in snapshot["actions"]:
                text = (action_text.get(action.get("group_key") or "") or action_text.get("tool:" + action["tool"])
                        or action_text.get("default", ""))
                events.append((ACTION_GAP_SECONDS, "action", {**action, "narration": text}))
    if "gate" in step_text:
        events.append((MIN_STEP_SECONDS, "step", {
            "seq": len(snapshot["steps"]) + 1, "stage": "gate", "status": "done", "summary": "all actions pending approval",
            "input_summary": "", "reason": "", "error": None, "model": None, "tokens": 0, "cost_usd": 0.0,
            "duration_ms": 0, "ts_offset_ms": 0, "narration": step_text["gate"],
        }))
    events.append((0.2, "done", {"status": snapshot["status"], "message": snapshot.get("message"),
                                 "cost_usd": snapshot.get("cost_usd"), "tokens": snapshot.get("tokens"),
                                 "narration": narration.get("outro", "")}))
    return events


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream(snapshot: dict, speed: float = 1.0) -> AsyncIterator[str]:
    """Replay as SSE text frames. speed 1 is the recorded pace (capped), 0 sends everything at once."""
    for delay, event, data in build_events(snapshot):
        if speed > 0 and delay > 0:
            await asyncio.sleep(delay * speed)
        yield sse(event, data)
