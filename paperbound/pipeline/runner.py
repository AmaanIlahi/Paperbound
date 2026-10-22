import time
from dataclasses import dataclass, field
from typing import Callable

from paperbound.pipeline.extract import extract_lease
from paperbound.pipeline.obligations import derive_obligations
from paperbound.pipeline.types import DerivedObligation, StepRecord, ValidatedField
from paperbound.pipeline.validate import GROUNDING_THRESHOLD, validate_fields


@dataclass
class PipelineResult:
    status: str  # done | partial
    steps: list[StepRecord] = field(default_factory=list)
    fields: list[ValidatedField] = field(default_factory=list)
    obligations: list[DerivedObligation] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.steps), 6)

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.steps)


def make_parse_step(pages, duration_ms: int, reused: bool) -> StepRecord:
    routes = sorted({p.route for p in pages}) if pages and hasattr(pages[0], "route") else ["text_layer"]
    low = [p.page_no for p in pages if p.quality_score < 0.5]
    if reused:
        reason = "Same file (sha256) was parsed before, so the stored pages were reused."
    else:
        reason = f"Text layer found on all pages; no OCR needed (route: {', '.join(routes)})."
    if low:
        reason += f" Low text quality on page(s) {low}."
    return StepRecord(
        stage="parse",
        status="done",
        input_summary=f"{len(pages)} page(s)",
        output_summary=f"{len(pages)} page(s), {sum(len(p.words) for p in pages)} words",
        reason=reason,
        duration_ms=duration_ms,
    )


def run_pipeline(pages, parse_step: StepRecord, extractor: Callable = extract_lease) -> PipelineResult:
    """extract -> validate -> obligations over already-parsed pages. Never raises on extract failure."""
    result = PipelineResult(status="done", steps=[parse_step])
    clock = parse_step.duration_ms

    def timed(stage: str, fn, **step_kwargs):
        nonlocal clock
        started = time.perf_counter()
        step = StepRecord(stage=stage, status="done", ts_offset_ms=clock, **step_kwargs)
        try:
            out = fn(step)
        except Exception as exc:
            step.status, step.error = "failed", f"{type(exc).__name__}: {exc}"
            out = None
        step.duration_ms = int((time.perf_counter() - started) * 1000)
        clock += step.duration_ms
        result.steps.append(step)
        return out

    def do_extract(step: StepRecord):
        extraction = extractor(pages)
        step.model, step.tokens, step.cost_usd = extraction.model, extraction.tokens, extraction.cost_usd
        names = type(extraction.fields).model_fields
        found = sum(1 for name in names if getattr(extraction.fields, name).value is not None)
        step.output_summary = f"{found}/{len(names)} fields returned"
        step.reason = "Rules cannot read free-form lease clauses, so the LLM extracts fields with verbatim quotes."
        return extraction.fields

    lease = timed("extract", do_extract, input_summary=f"{len(pages)} page text(s)")
    if lease is None:
        result.status = "partial"
        for stage in ("validate", "obligations"):
            result.steps.append(
                StepRecord(stage=stage, status="skipped", ts_offset_ms=clock, reason="Extraction failed, nothing to check.")
            )
        return result

    def do_validate(step: StepRecord):
        validated = validate_fields(lease, pages)
        present = [f for f in validated if f.value is not None]
        grounded = [f for f in present if "ungrounded" not in f.flags]
        flagged = [f"{f.name}: {', '.join(f.flags)}" for f in validated if f.flags]
        step.output_summary = f"{len(grounded)}/{len(present)} fields grounded on the page"
        step.reason = (
            f"Each quote was fuzzy-matched against the page words (grounded at >= {GROUNDING_THRESHOLD}). "
            + (f"Flags: {'; '.join(flagged)}." if flagged else "No flags raised.")
        )
        return validated

    validated = timed("validate", do_validate, input_summary="extracted fields")
    if validated is None:
        result.status = "partial"
        result.steps.append(
            StepRecord(stage="obligations", status="skipped", ts_offset_ms=clock, reason="Validation failed.")
        )
        return result
    result.fields = validated

    def do_obligations(step: StepRecord):
        derived, skipped = derive_obligations(validated)
        step.output_summary = f"{len(derived)} obligation(s)"
        step.reason = (
            "; ".join(f"{o.description}" for o in derived) if derived else ""
        ) + ("; " if derived and skipped else "") + "; ".join(skipped)
        return derived

    derived = timed("obligations", do_obligations, input_summary="validated fields")
    if derived is None:
        result.status = "partial"
    else:
        result.obligations = derived
    return result
