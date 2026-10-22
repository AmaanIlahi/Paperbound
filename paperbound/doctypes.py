"""What the pipeline does for each supported document type. Anything not listed here stops after classification."""

from dataclasses import dataclass
from typing import Callable

from paperbound.pipeline.extract import extract_coi, extract_lease
from paperbound.pipeline.impact import build_brief, build_coi_brief
from paperbound.pipeline.obligations import derive_coi_obligations, derive_obligations
from paperbound.pipeline.validate import validate_coi, validate_fields


@dataclass(frozen=True)
class DocType:
    name: str
    label: str
    extract: Callable  # (pages, today) -> ExtractionResult
    validate: Callable  # (extracted model, pages, today) -> list[ValidatedField]
    obligations: Callable  # (validated fields) -> (obligations, skipped reasons)
    brief: Callable  # (validated fields, today) -> Brief


SUPPORTED: dict[str, DocType] = {
    "lease": DocType(
        "lease", "Commercial lease",
        lambda pages, today: extract_lease(pages, today=today), validate_fields, derive_obligations, build_brief,
    ),
    "insurance_certificate": DocType(
        "insurance_certificate", "Certificate of liability insurance",
        lambda pages, today: extract_coi(pages, today=today), validate_coi, derive_coi_obligations, build_coi_brief,
    ),
}

UNSUPPORTED_MESSAGE = "document type not supported yet"
