from types import SimpleNamespace

from paperbound.pipeline.extract import ExtractionResult
from paperbound.pipeline.runner import make_parse_step, run_pipeline
from paperbound.schemas.lease import LeaseFields

TEXT = (
    "Landlord Harborview Properties LLC. The Term expires on March 31, 2027. "
    "Tenant must give ninety (90) days written notice."
)
WORDS = [{"text": w, "bbox": [i * 10, 0, i * 10 + 9, 10]} for i, w in enumerate(TEXT.split())]
PAGES = [SimpleNamespace(page_no=1, route="text_layer", text=TEXT, words=WORDS, quality_score=1.0)]


def none_field(**extra):
    return {"value": None, "source_quote": None, "page": None, **extra}


def fake_extractor(pages):
    lease = LeaseFields.model_validate(
        {
            "landlord": {"value": "Harborview Properties LLC", "source_quote": "Landlord Harborview Properties LLC.", "page": 1},
            "tenant": none_field(),
            "premises_address": none_field(),
            "start_date": none_field(),
            "end_date": {"value": "2027-03-31", "source_quote": "The Term expires on March 31, 2027.", "page": 1},
            "rent_amount": none_field(),
            "rent_period": none_field(),
            "notice_period_days": {
                "value": 90,
                "source_quote": "Tenant must give ninety (90) days written notice.",
                "page": 1,
            },
        }
    )
    return ExtractionResult(lease, "fake-model", 100, 0.001)


def test_full_run_with_fake_extractor():
    result = run_pipeline(PAGES, make_parse_step(PAGES, 5, reused=False), extractor=fake_extractor)
    assert result.status == "done"
    assert [s.stage for s in result.steps] == ["parse", "extract", "validate", "obligations"]
    assert all(s.status == "done" for s in result.steps)
    assert result.cost_usd == 0.001 and result.tokens == 100
    assert [o.due_date.isoformat() for o in result.obligations] == ["2026-12-31"]
    offsets = [s.ts_offset_ms for s in result.steps]
    assert offsets == sorted(offsets)


def test_extract_failure_gives_partial_run_and_skips_later_steps():
    def boom(pages):
        raise RuntimeError("api down")

    result = run_pipeline(PAGES, make_parse_step(PAGES, 5, reused=False), extractor=boom)
    assert result.status == "partial"
    statuses = {s.stage: s.status for s in result.steps}
    assert statuses == {"parse": "done", "extract": "failed", "validate": "skipped", "obligations": "skipped"}
    assert "api down" in next(s for s in result.steps if s.stage == "extract").error
    assert result.fields == [] and result.obligations == []
