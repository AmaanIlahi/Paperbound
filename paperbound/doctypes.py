"""What the pipeline does for each document type: the typed ones, and the generic fallback for everything else."""

from dataclasses import dataclass
from typing import Callable

from paperbound.pipeline.extract import extract_coi, extract_fallback, extract_lease
from paperbound.pipeline.generic import build_generic_brief, derive_generic_obligations
from paperbound.pipeline.impact import build_brief, build_coi_brief
from paperbound.pipeline.obligations import derive_coi_obligations, derive_obligations
from paperbound.pipeline.validate import validate_coi, validate_fields, validate_generic
from paperbound.schemas.insurance import COI_ANCHORS
from paperbound.schemas.lease import LEASE_ANCHORS


@dataclass(frozen=True)
class DocType:
    name: str
    label: str
    extract: Callable  # (pages, today) -> ExtractionResult
    validate: Callable  # (extracted model, pages, today) -> list[ValidatedField]
    obligations: Callable  # (validated fields) -> (obligations, skipped reasons)
    brief: Callable  # (validated fields, today) -> Brief
    anchors: dict | None = None  # field -> words its quote (or sentence) must contain; None: no schema, no check (generic)


SUPPORTED: dict[str, DocType] = {
    "lease": DocType(
        "lease", "Commercial lease",
        lambda pages, today: extract_lease(pages, today=today), validate_fields, derive_obligations, build_brief, LEASE_ANCHORS,
    ),
    "insurance_certificate": DocType(
        "insurance_certificate", "Certificate of liability insurance",
        lambda pages, today: extract_coi(pages, today=today), validate_coi, derive_coi_obligations, build_coi_brief, COI_ANCHORS,
    ),
}

# A document the classifier calls unsupported follows this path. Its type label is display text and never selects a spec.
GENERIC = DocType(
    "generic", "Document of an unsupported type",
    lambda pages, today: extract_fallback(pages, today=today), validate_generic, derive_generic_obligations, build_generic_brief,
)


def spec_for(doc_type: str | None) -> DocType | None:
    return SUPPORTED.get(doc_type or "") or (GENERIC if doc_type == "generic" else None)


UNSUPPORTED_MESSAGE = "document type not supported yet"
