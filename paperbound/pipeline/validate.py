import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from paperbound import clock, config
from paperbound.pipeline.types import ValidatedField
from paperbound.schemas.lease import LeaseFields

GROUNDING_THRESHOLD = 0.8
SHORT_QUOTE_TOKENS = 3  # quotes this short are too easy to fuzzy-match to the wrong place, so they must match exactly
W_GROUNDING, W_VALIDATORS, W_QUALITY = 0.6, 0.3, 0.1
SUSPICIOUS_CONFIDENCE_CAP = 0.3
# Any of these flags means the validator weight is dropped from the confidence.
CHECK_FLAGS = {
    "invalid_date", "end_before_start", "invalid_amount", "invalid_notice_period", "invalid_percent",
    "value_not_in_quote", "quote_rule_failed", "conflicting_values", "notice_unit_business_days", "invalid_currency",
    "amount_mismatch",
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
DATE_FIELDS = ("start_date", "end_date", "effective_date", "expiration_date", "date", "due_date")  # last two: generic fallback
AMOUNT_FIELDS = ("rent_amount", "security_deposit", "current_rent", "each_occurrence_limit", "aggregate_limit", "amount")
PERCENT_FIELDS = ("holdover_rate_percent", "rent_escalation_percent")
_PERIOD_WORDS = {
    "monthly": ("month", "monthly"),
    "quarterly": ("quarter",),
    "annual": ("year", "annual", "annum"),
}


def rule_name(name: str) -> str:
    """Rules are keyed by the field's own name: 'policy_2.expiration_date' is checked like 'expiration_date'."""
    return name.rsplit(".", 1)[-1]


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

# Month names, full or abbreviated ("Nov", "Nov.", "Sept"); the month is the first three letters.
_MONTH_WORD = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s*\.)?"
)
_MONTH_NUMBER = {name[:3]: number for name, number in MONTHS.items()}


def _month_of(word: str) -> int:
    return _MONTH_NUMBER[word.lower()[:3]]


_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))


def squash(text: str) -> str:
    """Quote text as the parsers read it: NFKC (no-break and thin spaces become spaces, fullwidth digits and punctuation
    become ASCII) with zero-width characters removed, so the same words read the same wherever the PDF was parsed."""
    return unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH)


def _dates_in(text: str) -> set[str]:
    """Every full calendar date a text states, as ISO: 2026-11-15, 2026/11/15, 11/15/2026 (US order), November 15, 2026,
    Nov. 15, 2026, 15 Nov 2026, 15th of November 2026. Spacing around commas and periods is free ("March 3 , 2020",
    "Nov.15,2026") and days may be zero-padded ("March 03,2020")."""
    text = squash(text)
    found = set()
    for y, m, d in re.findall(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text):
        found.add((int(y), int(m), int(d)))
    for m, d, y in re.findall(r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})\b", text):
        found.add((int(y), int(m), int(d)))
    for m, d, y in re.findall(rf"\b({_MONTH_WORD})\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})\b", text, re.I):
        found.add((int(y), _month_of(m), int(d)))
    for d, m, y in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_WORD})\s*,?\s*(\d{{4}})\b", text, re.I):
        found.add((int(y), _month_of(m), int(d)))
    out = set()
    for y, m, d in found:
        try:
            out.add(date(y, m, d).isoformat())
        except ValueError:
            pass
    return out


_CURRENCY_CODES = r"(?:USD|EUR|GBP|CAD|AUD|CHF|JPY|INR|MXN|CNY)"
_MONEY_RE = re.compile(
    rf"(?P<open>\()?\s*(?P<neg>-)?\s*(?:(?P<pre>US\$|[$€£¥]|{_CURRENCY_CODES}\b)\s*)?(?P<neg2>-)?"
    rf"(?P<num>\d+(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    rf"(?:\s*(?P<post>{_CURRENCY_CODES}\b))?\s*(?P<close>\))?",
    re.I,
)


_RESTATES_RE = re.compile(r"\b(?:dollars?|euros?|pounds?|cents|usd|eur|gbp)$", re.I)


def amounts_in(text: str) -> list[float]:
    """Monetary amounts a text states, parsed by code: $8,500.00, USD 8500, 250,000.00 USD, (1,200.00) as -1200.

    Only figures that carry a currency sign or code count; when none do, every figure does (a quote such as
    "Rent: 8,500.00"). A money figure in parentheses is negative (accounting style) unless it restates words before
    it, as in "Eight Thousand Dollars ($8,500.00)"."""
    found = []
    text = squash(text)
    for m in _MONEY_RE.finditer(text):
        number = float(m.group("num").replace(",", ""))
        marked = bool(m.group("pre") or m.group("post"))
        before = text[: m.start("open")].rstrip() if m.group("open") else ""
        restates = bool(_RESTATES_RE.search(before))  # "Eight Thousand Dollars ($8,500.00)"
        looks_like_money = marked or "," in m.group("num") or "." in m.group("num")  # not "ninety (90) days"
        accounting = bool(m.group("open") and m.group("close") and looks_like_money and not restates)
        negative = bool(m.group("neg") or m.group("neg2")) or accounting
        found.append((-number if negative else number, marked))
    marked_only = [v for v, marked in found if marked]
    return marked_only or [v for v, _ in found]


def reconcile_amount(model_value, quote: str | None) -> tuple[object, bool]:
    """(value to use, mismatch). The value comes from the quote when the quote states exactly one amount; the model's
    value is then only a cross-check, and a difference is a mismatch. With several amounts in the quote the model's
    value must be one of them and is kept; with none the model's value is kept for value_in_quote to judge."""
    candidates = sorted(set(amounts_in(quote or "")))
    if len(candidates) == 1 and isinstance(model_value, (int, float)):
        return candidates[0], abs(candidates[0] - float(model_value)) >= 0.005
    return model_value, False


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
    name = rule_name(name)
    if value is None:
        return True
    if not quote:
        return False
    quote = squash(quote)
    if name in DATE_FIELDS:
        return value in _dates_in(quote)
    if name in AMOUNT_FIELDS:
        return any(abs(n - float(value)) < 0.005 for n in _numbers_in(quote))
    if name in PERCENT_FIELDS:
        return any(abs(n - float(value)) < 0.005 for n in _numbers_in(quote)) or float(value) in _word_numbers(quote)
    if name == "notice_section":
        ident = re.sub(r"^\s*(?:section|article|clause|§)\s*", "", str(value), flags=re.I).strip().rstrip(".")
        return bool(ident) and bool(re.search(rf"(?<![\w.]){re.escape(ident)}(?![\w]|\.\d)", quote, re.I))
    if name == "notice_period_days":
        return float(value) in _numbers_in(quote) or int(value) in _word_numbers(quote)
    if name == "rent_period":
        return any(w in quote.casefold() for w in _PERIOD_WORDS.get(value, ()))
    if name == "description":  # a generic obligation is paraphrased; its quote is the evidence, its date is checked on its own
        return True
    needle, haystack = _norm(str(value)), _norm(quote)
    return bool(needle) and (needle in haystack or fuzz.partial_ratio(needle, haystack) >= 90)


# ---- type / range checks ----

def _normalize(name: str, value, flags: list[str]):
    """Return the normalized value; append a flag and keep the raw value if it fails a check."""
    name = rule_name(name)
    if value is None:
        return None
    if name in DATE_FIELDS:
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            flags.append("invalid_date")
            return value
    if name in AMOUNT_FIELDS:
        if not isinstance(value, (int, float)) or value <= 0:
            flags.append("invalid_amount")
        return float(value) if isinstance(value, (int, float)) else value
    if name in PERCENT_FIELDS:
        if not isinstance(value, (int, float)) or not 0 < value <= 1000:
            flags.append("invalid_percent")
        return float(value) if isinstance(value, (int, float)) else value
    if name == "notice_period_days":
        if not isinstance(value, int) or not 1 <= value <= 365:
            flags.append("invalid_notice_period")
        return value
    if name == "notice_section" and isinstance(value, str):
        lead = re.match(r"\s*(?:section\s+)?(\d+(?:\.\d+)*)(?![\w])", value, re.I)
        return f"Section {lead.group(1)}" if lead else value.strip()  # "11. Renewal; ..." -> "Section 11"
    return value.strip() if isinstance(value, str) else value


# ---- injection hardening: per-field quote rules, instruction detector, table cross-check ----

_CURRENCY_RE = re.compile(r"(?:\$|US\$|USD\s?)\s*\d", re.I)
_NUMBER_WORDS = "|".join([*_UNITS, *_TENS, "hundred"])
_PERCENT_RE = re.compile(r"%|\bper\s?cent\b", re.I)
_DELIVERY_RE = re.compile(r"\b(?:mail|courier|deliver\w*|e-?mail|fax|hand|person|post\w*|registered|writing|written)\b", re.I)
_SECTION_RE = re.compile(r"\b(?i:section|article|clause)\b|§|(?:^|\s)\d+(?:\.\d+)*\.?\s+[A-Z]")
_DAYS_RE = re.compile(  # "90 days", "90-day", "ninety (90) calendar days", "ninety (90) business days"
    rf"(?:\d+|\b(?:{_NUMBER_WORDS})\b)\)?\s*-?\s*(?:(?:calendar|business|working|consecutive)\s+)?days?\b", re.I
)
_BUSINESS_DAYS_RE = re.compile(rf"(?:\d+|\b(?:{_NUMBER_WORDS})\b)\)?\s*-?\s*(?:business|working)\s+days?\b", re.I)
_INSTRUCTION_RE = re.compile(
    r"\b(?:ignore|disregard|instructions?|assistant)\b|\bsystem\s*:|\bset\s+[\w.]+\s+to\b", re.I
)
_RENT_TABLE_RE = re.compile(
    r"year\s+period\s+(?:monthly\s+)?(?:base\s+)?rent\s+(?:year\s+)?1\s+[^$]{0,60}?\$\s*([\d,]+(?:\.\d+)?)", re.I
)


def quote_rule_ok(name: str, quote: str | None) -> bool:
    """A quote must look like the kind of text that can state this field."""
    name = rule_name(name)
    if not quote:
        return False
    quote = squash(quote)
    if name == "number":
        return any(ch.isdigit() for ch in quote)
    if name == "amount":  # generic fallback: any currency, so only require a figure (value_in_quote checks which one)
        return any(ch.isdigit() for ch in quote)
    if name in AMOUNT_FIELDS:
        return bool(_CURRENCY_RE.search(quote))
    if name in PERCENT_FIELDS:
        return bool(_PERCENT_RE.search(quote))
    if name == "notice_method":
        return bool(_DELIVERY_RE.search(quote))
    if name == "notice_section":
        return bool(_SECTION_RE.search(quote))
    if name in DATE_FIELDS:
        return bool(_dates_in(quote))
    if name == "notice_period_days":
        return bool(_DAYS_RE.search(quote))
    return True


def looks_like_instruction(quote: str | None) -> bool:
    return bool(quote and _INSTRUCTION_RE.search(squash(quote)))


def rent_table_first_amount(pages) -> float | None:
    """Amount in the first row of a rent escalation table ('Lease Year | Period | Monthly Base Rent')."""
    for page in pages:
        match = _RENT_TABLE_RE.search(" ".join(page.text.split()))
        if match:
            return float(match.group(1).replace(",", ""))
    return None


_ROW_RE = re.compile(
    r"\b(\d{1,2})\s+([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\s*[-–]\s*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\s+\$\s*([\d,]+(?:\.\d+)?)"
)
_LOOSE_DATE_RE = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")
_MONTH_BY_PREFIX = {m[:3]: n for m, n in MONTHS.items()}


def _loose_date(text: str) -> date | None:
    m = _LOOSE_DATE_RE.fullmatch(text.strip())
    if not m or m.group(1).lower()[:3] not in _MONTH_BY_PREFIX:
        return None
    try:
        return date(int(m.group(3)), _MONTH_BY_PREFIX[m.group(1).lower()[:3]], int(m.group(2)))
    except ValueError:
        return None


def rent_table_rows(pages) -> list[tuple[date, date, float]]:
    """(period start, period end, monthly amount) for each row of a rent escalation table."""
    rows = []
    for page in pages:
        for _, start, end, amount in _ROW_RE.findall(" ".join(page.text.split())):
            s, e = _loose_date(start), _loose_date(end)
            if s and e:
                rows.append((s, e, float(amount.replace(",", ""))))
    return rows


def rent_table_amount_on(pages, day: date) -> float | None:
    for start, end, amount in rent_table_rows(pages):
        if start <= day <= end:
            return amount
    return None


def _validate_items(items, pages) -> list[ValidatedField]:
    """Ground and check each (name, extracted field) pair. Cross-field checks and confidence come afterwards."""
    results: list[ValidatedField] = []
    for name, extracted in items:
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
        short = bool(quote) and len(_tokens(quote)) <= SHORT_QUOTE_TOKENS
        if score < GROUNDING_THRESHOLD or (short and score < 1.0):  # a short quote must match exactly
            flags.append("ungrounded")
            bboxes = []
        elif extracted.page is not None and page_no != extracted.page:
            flags.append("page_corrected")
        if rule_name(name) in AMOUNT_FIELDS and quote and "ungrounded" not in flags:
            value, mismatch = reconcile_amount(value, quote)
            if mismatch:
                flags.append("amount_mismatch")
            if isinstance(value, (int, float)) and value <= 0 and "invalid_amount" not in flags:
                flags.append("invalid_amount")
        if not value_in_quote(name, value, quote):
            flags.append("value_not_in_quote")
        if not quote_rule_ok(name, quote):
            flags.append("quote_rule_failed")
        if rule_name(name) == "notice_period_days" and quote and _BUSINESS_DAYS_RE.search(quote):
            flags.append("notice_unit_business_days")  # a business-day deadline needs a holiday calendar: not derived
        if looks_like_instruction(quote):
            flags.append("suspicious_instruction")

        results.append(
            ValidatedField(name, value, quote, page_no, union_bbox(bboxes), 0.0, score, flags, False, bboxes)
        )
    return results


def _finish(results: list[ValidatedField], pages) -> list[ValidatedField]:
    """validators_passed and confidence for every field (run after the cross-field checks)."""
    pages_by_no = {p.page_no: p for p in pages}
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


def validate_fields(lease: LeaseFields, pages, today: date | None = None) -> list[ValidatedField]:
    today = clock.today(today)
    results = _validate_items([(name, getattr(lease, name)) for name in LeaseFields.model_fields], pages)

    by_name = {r.name: r for r in results}
    rent = by_name["rent_amount"]
    table_amount = rent_table_first_amount(pages)
    if isinstance(rent.value, (int, float)) and table_amount is not None and abs(rent.value - table_amount) > 0.005:
        rent.flags.append("conflicting_values")
    current = by_name["current_rent"]
    table_today = rent_table_amount_on(pages, today)
    if isinstance(current.value, (int, float)) and table_today is not None and abs(current.value - table_today) > 0.005:
        current.flags.append("conflicting_values")
    start, end = by_name["start_date"], by_name["end_date"]
    if (
        start.value and end.value
        and "invalid_date" not in start.flags + end.flags
        and date.fromisoformat(end.value) <= date.fromisoformat(start.value)
    ):
        start.flags.append("end_before_start")
        end.flags.append("end_before_start")

    return _finish(results, pages)


def validate_coi(cert, pages, today: date | None = None) -> list[ValidatedField]:
    """Validate a certificate of insurance. Policy fields are named policy_1.number, policy_1.expiration_date, ..."""
    clock.today(today)  # certificates have no today-dependent check yet; resolving it keeps AS_OF errors loud
    items = [(name, getattr(cert, name)) for name in ("insured", "certificate_holder", "producer")]
    for i, policy in enumerate(cert.policies, start=1):
        items += [(f"policy_{i}.{name}", getattr(policy, name)) for name in type(policy).model_fields]
    results = _validate_items(items, pages)
    by_name = {r.name: r for r in results}
    for i in range(1, len(cert.policies) + 1):
        start, end = by_name[f"policy_{i}.effective_date"], by_name[f"policy_{i}.expiration_date"]
        if (
            start.value and end.value
            and "invalid_date" not in start.flags + end.flags
            and date.fromisoformat(end.value) <= date.fromisoformat(start.value)
        ):
            start.flags.append("end_before_start")
            end.flags.append("end_before_start")
    return _finish(results, pages)


# ---- generic fallback ----

GENERIC_LIST_LIMIT = 10  # items kept per list, in the order returned
_CURRENCY_CODE_RE = re.compile(r"[A-Z]{3}")


@dataclass
class _Item:
    """A generic extracted value in the shape _validate_items reads."""

    value: object
    source_quote: str | None
    page: int | None


def validate_generic(extracted, pages, today: date | None = None) -> list[ValidatedField]:
    """Validate a generic extraction (FallbackFields) with the same grounding, value-in-quote and instruction checks as
    the typed schemas. Fields are named party_1.name, date_1.date, amount_1.amount, obligation_1.description and
    obligation_1.due_date (which shares the obligation's quote). Confidence is capped (GENERIC_CONFIDENCE_CAP) so a
    generic field never looks as trusted as a typed one."""
    clock.today(today)  # resolving it keeps AS_OF errors loud, like the typed validators
    items, labels, label_texts = [], {}, {}
    limit = GENERIC_LIST_LIMIT
    for i, party in enumerate(extracted.parties[:limit], start=1):
        name = f"party_{i}.name"
        items.append((name, _Item(party.name, party.source_quote, party.page)))
        labels[name] = party.role
    for i, item in enumerate(extracted.dates[:limit], start=1):
        name = f"date_{i}.date"
        items.append((name, _Item(item.value, item.source_quote, item.page)))
        labels[name] = item.label
    for i, item in enumerate(extracted.amounts[:limit], start=1):
        name = f"amount_{i}.amount"
        items.append((name, _Item(item.value, item.source_quote, item.page)))
        labels[name] = f"{item.label} ({item.currency})" if item.currency else item.label
        label_texts[name] = item.currency
    for i, item in enumerate(extracted.obligations[:limit], start=1):
        items.append((f"obligation_{i}.description", _Item(item.description, item.source_quote, item.page)))
        if item.due_date:
            items.append((f"obligation_{i}.due_date", _Item(item.due_date, item.source_quote, item.page)))
            labels[f"obligation_{i}.due_date"] = "due date"
    results = _validate_items(items, pages)
    for r in results:
        r.label = (" ".join(str(labels.get(r.name) or "").split()) or None)
        if r.value is None:
            continue
        # The label (and an obligation's description) are the model's words about untrusted text: screen them too.
        if looks_like_instruction(r.label) or (r.name.endswith(".description") and looks_like_instruction(str(r.value))):
            r.flags.append("suspicious_instruction")
        currency = label_texts.get(r.name)
        if r.name.startswith("amount_") and currency is not None and not _CURRENCY_CODE_RE.fullmatch(currency):
            r.flags.append("invalid_currency")
    _finish(results, pages)
    cap = config.generic_confidence_cap()
    for r in results:
        r.confidence = min(r.confidence, cap)
    return results
