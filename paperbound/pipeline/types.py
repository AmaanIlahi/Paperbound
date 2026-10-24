from dataclasses import dataclass, field
from datetime import date


@dataclass
class StepRecord:
    stage: str
    status: str  # done | failed | skipped
    input_summary: str = ""
    output_summary: str = ""
    reason: str = ""
    model: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    ts_offset_ms: int = 0
    error: str | None = None


@dataclass
class ValidatedField:
    name: str
    value: object  # normalized: ISO date string, float, int or str; None if not found
    source_quote: str | None
    page_no: int | None
    bbox: list[float] | None  # union of bboxes
    confidence: float
    grounding_score: float
    flags: list[str] = field(default_factory=list)
    validators_passed: bool = False
    bboxes: list[list[float]] = field(default_factory=list)  # one box per matched line
    label: str | None = None  # generic fallback fields: what the value is ("due date"), a display string from the model


@dataclass
class DerivedObligation:
    kind: str
    due_date: date
    description: str
    source_fields: list[str]
