import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from paperbound import clock
from paperbound.agent.generic_planner import plan_generic_actions
from paperbound.agent.planner import PlanResult, plan_actions
from paperbound.doctypes import UNSUPPORTED_MESSAGE, spec_for
from paperbound.pipeline.classify import Classification, classify_document
from paperbound.pipeline.impact import Brief
from paperbound.pipeline.types import DerivedObligation, StepRecord, ValidatedField
from paperbound.pipeline.validate import GROUNDING_THRESHOLD


@dataclass
class PipelineResult:
    status: str  # done | partial
    steps: list[StepRecord] = field(default_factory=list)
    fields: list[ValidatedField] = field(default_factory=list)
    obligations: list[DerivedObligation] = field(default_factory=list)
    brief: Brief | None = None
    plan: PlanResult | None = None
    doc_type: str | None = None
    classification: Classification | None = None
    message: str | None = None  # set when the run stops early on purpose
    extracted_present: int = 0  # fields the model returned a value for (known before validation)
    type_label: str | None = None  # generic documents: the classifier's guess at the type, for display only

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


def count_present(model) -> int:
    """Fields with a value in an extraction result, counting inside lists (certificate policies)."""
    total = 0
    for name in type(model).model_fields:
        item = getattr(model, name)
        if isinstance(item, list):
            total += sum(count_present(x) for x in item)
        elif hasattr(item, "value") and item.value is not None:
            total += 1
    return total


def count_generic(model) -> int:
    """Items in an extraction of the generic fallback: every entry of every list."""
    return sum(len(getattr(model, name)) for name in type(model).model_fields)


def _has_value(item) -> bool:
    """Top-level extracted field found? A list (policies) counts when non-empty."""
    return bool(item) if isinstance(item, list) else item.value is not None


def run_pipeline(
    pages,
    parse_step: StepRecord,
    extractor: Callable | None = None,
    planner: Callable = plan_actions,
    existing_obligations: list[dict] | None = None,
    today: date | None = None,
    classifier: Callable = classify_document,
    observer: Callable | None = None,
    generic_planner: Callable = plan_generic_actions,
    pdf: bytes | None = None,
) -> PipelineResult:
    """classify -> extract -> validate -> obligations -> impact -> planner over already-parsed pages.

    Never raises on stage failure. `extractor(pages)` overrides the document type's own extractor (used by tests).
    A document the classifier calls unsupported is `generic` and follows the same stages with the generic schema
    (`generic_planner` stands in for the planner; it is deterministic and calls no model).
    Tabular types (rent roll, T-12) detect their tables in `pdf`, the file's bytes; their planner is code, not a model.
    `observer(event, stage, step, result)` is told when a stage starts ("start", step None) and when it has been
    appended to result.steps ("end", including skipped stages); result already holds that stage's output.
    """
    today = clock.today(today)
    result = PipelineResult(status="done", steps=[parse_step])
    elapsed = parse_step.duration_ms

    def notify(event: str, stage: str, step: StepRecord | None):
        if observer:
            observer(event, stage, step, result)

    def timed(stage: str, fn, **step_kwargs):
        nonlocal elapsed
        started = time.perf_counter()
        step = StepRecord(stage=stage, status="done", ts_offset_ms=elapsed, **step_kwargs)
        notify("start", stage, None)
        try:
            out = fn(step)
        except Exception as exc:
            step.status, step.error = "failed", f"{type(exc).__name__}: {exc}"
            out = None
        step.duration_ms = int((time.perf_counter() - started) * 1000)
        elapsed += step.duration_ms
        result.steps.append(step)
        notify("end", stage, step)
        return out

    def skip_rest(stages, reason):
        result.status = "partial"
        for stage in stages:
            result.steps.append(StepRecord(stage=stage, status="skipped", ts_offset_ms=elapsed, reason=reason))
            notify("end", stage, result.steps[-1])

    def do_classify(step: StepRecord):
        found = classifier(pages)
        result.classification, result.doc_type = found, found.doc_type
        step.model, step.tokens, step.cost_usd = found.model, found.tokens, found.cost_usd
        step.output_summary = f"{found.doc_type} ({found.method})"
        result.type_label = found.type_label
        step.reason = found.reason + (
            "" if found.doc_type != "generic"
            else f" No typed schema fits ({found.type_label or 'unknown'}), so the generic fallback runs: confidence is capped and only reminders and a review flag can be proposed."
        )
        return found

    result.classification = timed("classify", do_classify, input_summary=f"{len(pages)} page(s) of text")
    if result.classification is None:
        skip_rest(("extract", "validate", "obligations", "impact", "planner"), "Classification failed, so the document type is unknown.")
        return result
    result.doc_type = result.classification.doc_type
    spec = spec_for(result.doc_type)
    if spec is None:
        result.message = UNSUPPORTED_MESSAGE
        return result

    def do_extract(step: StepRecord):
        if extractor:
            extraction = extractor(pages)
        else:
            extraction = spec.extract(pages, today, pdf) if spec.table else spec.extract(pages, today)
        step.model, step.tokens, step.cost_usd = extraction.model or None, extraction.tokens, extraction.cost_usd
        extracted = extraction.fields
        if spec.table:
            result.extracted_present = extracted.count()
            step.output_summary = extracted.summary()
            step.reason = extracted.explain()
            return extracted
        names = type(extracted).model_fields
        if spec.name == "generic":
            result.extracted_present = count_generic(extracted)
            step.output_summary = ", ".join(f"{len(getattr(extracted, n))} {n}" for n in names) + " returned"
        else:
            result.extracted_present = count_present(extracted)
            step.output_summary = f"{sum(1 for n in names if _has_value(getattr(extracted, n)))}/{len(names)} top-level fields returned"
        step.reason = f"Rules cannot read free-form {spec.label.lower()} text, so the LLM extracts fields with verbatim quotes."
        return extracted

    extracted = timed("extract", do_extract, input_summary=f"{len(pages)} page text(s), as of {today.isoformat()}")
    if extracted is None:
        skip_rest(("validate", "obligations", "impact", "planner"), "Extraction failed, nothing to check.")
        return result

    def do_validate(step: StepRecord):
        validated = spec.validate(extracted, pages, today)
        present = [f for f in validated if f.value is not None]
        grounded = [f for f in present if "ungrounded" not in f.flags]
        flagged = [f"{f.name}: {', '.join(f.flags)}" for f in validated if f.flags]
        result.fields = validated
        step.output_summary = f"{len(grounded)}/{len(present)} fields grounded on the page"
        step.reason = (
            f"Each quote was fuzzy-matched against the page words (grounded at >= {GROUNDING_THRESHOLD}). "
            + (f"Flags: {'; '.join(flagged)}." if flagged else "No flags raised.")
        )
        return validated

    validated = timed("validate", do_validate, input_summary="extracted fields")
    if validated is None:
        skip_rest(("obligations", "impact", "planner"), "Validation failed.")
        return result
    result.fields = validated

    def do_obligations(step: StepRecord):
        derived, skipped = spec.obligations(validated, today) if spec.table else spec.obligations(validated)  # a table's dates are counted from this run's `today`
        result.obligations = derived
        step.output_summary = f"{len(derived)} obligation(s)"
        step.reason = (
            "; ".join(f"{o.description}" for o in derived) if derived else ""
        ) + ("; " if derived and skipped else "") + "; ".join(skipped)
        return derived

    derived = timed("obligations", do_obligations, input_summary="validated fields")
    if derived is None:
        skip_rest(("impact", "planner"), "Obligations failed.")
        return result
    result.obligations = derived

    def do_impact(step: StepRecord):
        brief = spec.brief(validated, today)
        result.brief = brief
        step.output_summary = f"{len(brief.lines)} line(s), {len(brief.skipped)} skipped"
        step.reason = (
            "Computed by code from grounded fields only; no LLM. "
            + ("Skipped: " + "; ".join(f"{s['key']} ({s['reason']})" for s in brief.skipped) + "." if brief.skipped else "")
        ).strip()
        return brief

    result.brief = timed("impact", do_impact, input_summary="validated fields, obligations")
    if result.brief is None:
        result.status = "partial"

    is_generic = spec.name == "generic"
    if not derived and not is_generic and spec.planner is None:  # the generic and tabular paths are code and may still propose a review flag
        result.steps.append(
            StepRecord(stage="planner", status="skipped", ts_offset_ms=elapsed, reason="No obligations, so there is nothing to plan.")
        )
        notify("end", "planner", result.steps[-1])
        return result

    def do_planner(step: StepRecord):
        if is_generic:
            plan = generic_planner(validated, derived, existing_obligations, brief=result.brief, today=today, type_label=result.type_label)
        elif spec.planner is not None:
            plan = spec.planner(validated, derived, existing_obligations, brief=result.brief, today=today, doc_type=spec.name)
        else:
            plan = planner(validated, derived, existing_obligations, brief=result.brief, today=today, doc_type=spec.name)
        result.plan = plan
        step.model, step.tokens, step.cost_usd = plan.model, plan.tokens, plan.cost_usd
        step.output_summary = f"{len(plan.actions)} action(s) proposed, {len(plan.rejected)} rejected"
        step.reason = (
            ("Built by code with no model: a reminder per usable future deadline and a review flag. " if is_generic
             else "Built by code with no model: reminders for usable leases that expire soon, and a review flag if any check failed. " if spec.planner is not None
             else "The agent only proposes; every side-effect action waits for approval. ")
            + f"{plan.api_calls} model call(s), {plan.tool_calls} tool call(s)."
            + (f" Rejected: {'; '.join(r['tool'] + ': ' + r['reason'] for r in plan.rejected)}." if plan.rejected else "")
            + (f" {' '.join(plan.notes)}" if plan.notes else "")
        )
        return plan

    plan = timed("planner", do_planner, input_summary=f"{len(derived)} obligation(s), usable fields only")
    if plan is None:
        result.status = "partial"
    else:
        result.plan = plan
    return result
