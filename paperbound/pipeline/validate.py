import re
from datetime import date

from rapidfuzz import fuzz

from paperbound.pipeline.types import ValidatedField
from paperbound.schemas.lease import LeaseFields

GROUNDING_THRESHOLD = 0.8
W_GROUNDING, W_VALIDATORS, W_QUALITY = 0.6, 0.3, 0.1
SUSPICIOUS_CONFIDENCE_CAP = 0.3
# Any of these flags means the validator weight is dropped from the confidence.
CHECK_FLAGS = {
    "invalid_date", "end_before_start", "invalid_amount", "invalid_notice_period",
    "value_not_in_quote", "quote_rule_failed", "conflicting_values",
}
# Fields carrying any of these flags are never used for obligations or actions.
BLOCKING_FLAGS = CHECK_FLAGS | {"ungrounded", "suspicious_instruction", "not_found"}

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"]
    )
}
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_PERIOD_WORDS = {
    "monthly": ("month", "monthly"),
    "quarterly": ("quarter",),
    "annual": ("year", "annual", "annum"),
}


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def _tokens(text: str) -> list[str]:
    return [t for t in (_norm(w) for w in text.split()) if t]


def ground_quote(quote: str, pages, preferred_page: int | None = None):
    """Fuzzy-match a quote against each page's words (sliding window over consecutive words).

    Returns (score 0..1, page_no, per-line bboxes). The stated page is searched first; other pages are only
    searched when it does not reach the grounding threshold, so a wrong page number is recovered.
    """
    q_tokens = _tokens(quote or "")
    if not q_tokens:
        return 0.0, preferred_page, []
    q_str = " ".join(q_tokens)
    n = len(q_tokens)

    ordered = sorted(pages, key=lambda p: p.page_no != preferred_page)
    best = (0.0, preferred_page, [])
    for page in ordered:
        score, boxes = _best_window(q_str, n, page.words)
        if score > best[0]:
            best = (score, page.page_no, group_lines(boxes))
        if best[0] >= GROUNDING_THRESHOLD:
            break
    return best


def _best_window(q_str: str, n: int, words: list[dict]):
    indexed = [(_norm(w["text"]), w) for w in words]
    indexed = [(t, w) for t, w in indexed if t]
    best_score, best_boxes = 0.0, []
    for size in range(max(1, n - 2), n + 3):
        for start in range(0, len(indexed) - size + 1):
            window = indexed[start:start + size]
            score = fuzz.ratio(q_str, " ".join(t for t, _ in window), score_cutoff=best_score * 100) / 100
            if score > best_score:
                best_score = score
                best_boxes = [w["bbox"] for _, w in window]
    return best_score, best_boxes


def union_bbox(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def group_lines(boxes: list[list[float]]) -> list[list[float]]:
    """Group word boxes (in reading order) into one union box per text line."""
    lines: list[list[list[float]]] = []
    for box in boxes:
        center = (box[1] + box[3]) / 2
        height = max(box[3] - box[1], 1e-6)
        if lines:
            first = lines[-1][0]
            if abs(center - (first[1] + first[3]) / 2) <= 0.6 * height:
                lines[-1].append(box)
                continue
        lines.append([box])
    return [union_bbox(line) for line in lines]


def field_confidence(grounding: float, validators_passed: bool, page_quality: float) -> float:
    value = W_GROUNDING * grounding + W_VALIDATORS * (1.0 if validators_passed else 0.0) + W_QUALITY * page_quality
    return round(min(max(value, 0.0), 1.0), 4)


# ---- value derivable from quote ----

def _dates_in(text: str) -> set[str]:
    found = set()
    for y, m, d in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text):
        found.add((int(y), int(m), int(d)))
    for m, d, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        found.add((int(y), int(m), int(d)))
    month_re = "|".join(MONTHS)
    for m, d, y in re.findall(rf"\b({month_re})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", text, re.I):
        found.add((int(y), MONTHS[m.lower()], int(d)))
    for d, m, y in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_re}),?\s+(\d{{4}})\b", text, re.I):
        found.add((int(y), MONTHS[m.lower()], int(d)))
    out = set()
    for y, m, d in found:
        try:
            out.add(date(y, m, d).isoformat())
        except ValueError:
            pass
    return out


def _numbers_in(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text) if n.replace(",", "")}


def _word_numbers(text: str) -> set[int]:
    """Integers spelled out in words, e.g. 'ninety' -> 90, 'one hundred twenty' -> 120."""
    results, current, in_run = set(), 0, False
    for word in re.findall(r"[a-z]+", text.lower()) + [""]:
        if word in _UNITS:
            current += _UNITS[word]
            in_run = True
        elif word in _TENS:
            current += _TENS[word]
            in_run = True
        elif word == "hundred" and in_run:
            current = max(current, 1) * 100
        else:
            if in_run:
                results.add(current)
            current, in_run = 0, False
    return results


def value_in_quote(name: str, value, quote: str | None) -> bool:
    if value is None:
        return True
    if not quote:
        return False
    if name in ("start_date", "end_date"):
        return value in _dates_in(quote)
    if name == "rent_amount":
        return any(abs(n - float(value)) < 0.005 for n in _numbers_in(quote))
    if name == "notice_period_days":
        return float(value) in _numbers_in(quote) or int(value) in _word_numbers(quote)
    if name == "rent_period":
        return any(w in quote.casefold() for w in _PERIOD_WORDS.get(value, ()))
    needle, haystack = _norm(str(value)), _norm(quote)
    return bool(needle) and (needle in haystack or fuzz.partial_ratio(needle, haystack) >= 90)


# ---- type / range checks ----

def _normalize(name: str, value, flags: list[str]):
    """Return the normalized value; append a flag and keep the raw value if it fails a check."""
    if value is None:
        return None
    if name in ("start_date", "end_date"):
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            flags.append("invalid_date")
            return value
    if name == "rent_amount":
        if not isinstance(value, (int, float)) or value <= 0:
            flags.append("invalid_amount")
        return float(value) if isinstance(value, (int, float)) else value
    if name == "notice_period_days":
        if not isinstance(value, int) or not 1 <= value <= 365:
            flags.append("invalid_notice_period")
        return value
    return value.strip() if isinstance(value, str) else value


# ---- injection hardening: per-field quote rules, instruction detector, table cross-check ----

_CURRENCY_RE = re.compile(r"(?:\$|US\$|USD\s?)\s*\d", re.I)
_NUMBER_WORDS = "|".join([*_UNITS, *_TENS, "hundred"])
_DAYS_RE = re.compile(rf"(?:\d+|\b(?:{_NUMBER_WORDS})\b)\)?\s*-?\s*days?\b", re.I)
_INSTRUCTION_RE = re.compile(
    r"\b(?:ignore|disregard|instructions?|assistant)\b|\bsystem\s*:|\bset\s+[\w.]+\s+to\b", re.I
)
_RENT_TABLE_RE = re.compile(
    r"year\s+period\s+(?:monthly\s+)?(?:base\s+)?rent\s+(?:year\s+)?1\s+[^$]{0,60}?\$\s*([\d,]+(?:\.\d+)?)", re.I
)


def quote_rule_ok(name: str, quote: str | None) -> bool:
    """A quote must look like the kind of text that can state this field."""
    if not quote:
        return False
    if name == "rent_amount":
        return bool(_CURRENCY_RE.search(quote))
    if name in ("start_date", "end_date"):
        return bool(_dates_in(quote))
    if name == "notice_period_days":
        return bool(_DAYS_RE.search(quote))
    return True


def looks_like_instruction(quote: str | None) -> bool:
    return bool(quote and _INSTRUCTION_RE.search(quote))


def rent_table_first_amount(pages) -> float | None:
    """Amount in the first row of a rent escalation table ('Lease Year | Period | Monthly Base Rent')."""
    for page in pages:
        match = _RENT_TABLE_RE.search(" ".join(page.text.split()))
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def validate_fields(lease: LeaseFields, pages) -> list[ValidatedField]:
    pages_by_no = {p.page_no: p for p in pages}
    results: list[ValidatedField] = []

    for name in LeaseFields.model_fields:
        extracted = getattr(lease, name)
        flags: list[str] = []
        value = _normalize(name, extracted.value, flags)

        if extracted.value is None:
            results.append(ValidatedField(name, None, None, None, None, 0.0, 0.0, ["not_found"], False))
            continue

        quote = (extracted.source_quote or "").strip() or None
        if quote:
            score, page_no, bboxes = ground_quote(quote, pages, extracted.page)
        else:
            score, page_no, bboxes = 0.0, extracted.page, []
        if score < GROUNDING_THRESHOLD:
            flags.append("ungrounded")
            bboxes = []
        elif extracted.page is not None and page_no != extracted.page:
            flags.append("page_corrected")
        if not value_in_quote(name, value, quote):
            flags.append("value_not_in_quote")
        if not quote_rule_ok(name, quote):
            flags.append("quote_rule_failed")
        if looks_like_instruction(quote):
            flags.append("suspicious_instruction")

        results.append(
            ValidatedField(name, value, quote, page_no, union_bbox(bboxes), 0.0, score, flags, False, bboxes)
        )

    by_name = {r.name: r for r in results}
    rent = by_name["rent_amount"]
    table_amount = rent_table_first_amount(pages)
    if isinstance(rent.value, (int, float)) and table_amount is not None and abs(rent.value - table_amount) > 0.005:
        rent.flags.append("conflicting_values")
    start, end = by_name["start_date"], by_name["end_date"]
    if (
        start.value and end.value
        and "invalid_date" not in start.flags + end.flags
        and date.fromisoformat(end.value) <= date.fromisoformat(start.value)
    ):
        start.flags.append("end_before_start")
        end.flags.append("end_before_start")

    for r in results:
        if r.value is None:
            continue
        blank = isinstance(r.value, str) and not r.value.strip()
        r.validators_passed = not blank and not (set(r.flags) & CHECK_FLAGS)
        quality = pages_by_no[r.page_no].quality_score if r.page_no in pages_by_no else 0.0
        r.confidence = field_confidence(r.grounding_score, r.validators_passed, quality)
        if "suspicious_instruction" in r.flags:
            r.confidence = min(r.confidence, SUSPICIOUS_CONFIDENCE_CAP)
    return results
