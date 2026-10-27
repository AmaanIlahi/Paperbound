"""The property a rent roll or T-12 is about, read by code from the first lines of page 1 (name and street address).

Used only by the cross-check, to tell whether two documents are about the same property. No model. If the lines cannot be
found the field is simply absent and the cross-check says the identity could not be compared.
"""

import re

from rapidfuzz import fuzz

from paperbound.pipeline.types import ValidatedField
from paperbound.pipeline.validate import field_confidence, ground_quote, squash, union_bbox

SCAN_LINES = 14
NAME_MATCH = 85  # rapidfuzz token_set_ratio, normalised names
ADDRESS_MATCH = 85  # rapidfuzz token_sort_ratio, normalised addresses

_STREET = r"(?:street|st|road|rd|avenue|ave|boulevard|blvd|drive|dr|way|parkway|pkwy|lane|ln|plaza|court|ct|highway|hwy|circle|cir|place|pl|square|sq|trail|trl)"
ADDRESS_RE = re.compile(rf"^\W*\d{{1,6}}\s+[A-Za-z0-9.' #-]+?\b{_STREET}\b\.?(?:[,\s].*)?$|,\s*[A-Z]{{2}}\s+\d{{5}}(?:-\d{{4}})?\s*$", re.I)
_TITLE_TAIL = re.compile(
    r"\s*[-–—:|]\s*(?:rent\s+roll|operating\s+statement|t[\s-]?12|trailing|income\s+statement|statement|as\s+of|page\s+\d+).*$", re.I)
_GENERIC = re.compile(
    r"^\W*(?:rent\s+roll|operating\s+statement|t[\s-]?12|trailing|income\s+statement|statement|as\s+of|page\s+\d+|prepared|unaudited|"
    r"confidential|summary|schedule|account|line\s+item|suite|unit|tenant|space)\b", re.I)
_ABBREVIATIONS = {
    "st": "street", "rd": "road", "ave": "avenue", "blvd": "boulevard", "dr": "drive", "pkwy": "parkway", "ln": "lane", "ct": "court",
    "hwy": "highway", "cir": "circle", "pl": "place", "sq": "square", "trl": "trail", "n": "north", "s": "south", "e": "east", "w": "west",
}
_NAME_NOISE = {"the", "llc", "lp", "inc", "co", "company", "properties", "property", "apartments", "center", "centre"}


def normalise_address(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", squash(text).casefold()).split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def normalise_name(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", squash(text).casefold()).split()
    kept = [w for w in words if w not in _NAME_NOISE]
    return " ".join(kept or words)


def names_match(a: str, b: str) -> tuple[bool, int]:
    score = int(fuzz.token_set_ratio(normalise_name(a), normalise_name(b)))
    return score >= NAME_MATCH, score


def addresses_match(a: str, b: str) -> tuple[bool, int]:
    score = int(fuzz.token_sort_ratio(normalise_address(a), normalise_address(b)))
    return score >= ADDRESS_MATCH, score


def find_property(pages) -> dict | None:
    """{"name", "address", "lines"} from the first lines of page 1; None if neither can be found."""
    if not pages or not getattr(pages[0], "text", ""):
        return None
    lines = [ln.strip() for ln in pages[0].text.splitlines() if ln.strip()][:SCAN_LINES]
    address_at = next((i for i, ln in enumerate(lines) if ADDRESS_RE.search(ln)), None)
    cleaned = [(i, _TITLE_TAIL.sub("", ln).strip()) for i, ln in enumerate(lines)]
    names = [(i, c) for i, c in cleaned if len(re.findall(r"[A-Za-z]", c)) >= 3 and not _GENERIC.match(c) and not ADDRESS_RE.search(c)
             and not re.fullmatch(r"[\d\W]+", c)]
    name = None
    if address_at is not None:  # the line just above the address, else the first plausible one
        name = next(((i, c) for i, c in names if i == address_at - 1), None)
    name = name or (names[0] if names else None)
    if name is None and address_at is None:
        return None
    return {
        "name": name[1] if name else None, "address": lines[address_at] if address_at is not None else None,
        "lines": [lines[i] for i in sorted({name[0] if name else address_at, address_at if address_at is not None else name[0]})],
    }


def property_field(pages) -> ValidatedField | None:
    found = find_property(pages)
    if found is None:
        return None
    quote = " ".join(found["lines"])
    score, page_no, boxes = ground_quote(quote, pages, 1)
    page_quality = next((p.quality_score for p in pages if p.page_no == page_no), 0.0)
    return ValidatedField(
        "property", {"name": found["name"], "address": found["address"]}, quote, page_no if boxes else 1, union_bbox(boxes),
        round(field_confidence(score, True, page_quality), 4), score, [], True, boxes, found["name"] or found["address"],
    )
