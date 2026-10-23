"""Pure scoring helpers for the eval harness. No network, no database, no model."""

import math
import re
from collections import Counter
from datetime import date

from rapidfuzz import fuzz

from paperbound.pipeline.validate import MONTHS

FUZZY_THRESHOLD = 85
BUCKETS = ("<0.7", "0.7-0.9", ">=0.9")
REGRESSION_POINTS = 2.0  # a drop of more than this many percentage points is flagged

DATE_FIELDS = {"start_date", "end_date", "effective_date", "expiration_date"}
NUMBER_FIELDS = {
    "rent_amount", "security_deposit", "current_rent", "each_occurrence_limit", "aggregate_limit",
    "holdover_rate_percent", "rent_escalation_percent", "notice_period_days",
}
NAME_FIELDS = {"landlord", "tenant", "premises_address", "insured", "certificate_holder", "producer", "insurer"}
CONTAINS_FIELDS = {"notice_method", "type"}  # expected is a list of words that must all appear


def norm_date(value) -> str | None:
    """ISO date from an ISO string, 'March 31, 2027', '31 March 2027' or '03/31/2027'; None if it is not a full date."""
    if value is None:
        return None
    text = str(value).strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        y, mo, d = map(int, m.groups())
    elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
        mo, d, y = map(int, m.groups())
    elif m := re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", text):
        mo = MONTHS.get(m.group(1).lower(), 0) or {k[:3]: v for k, v in MONTHS.items()}.get(m.group(1).lower()[:3], 0)
        d, y = int(m.group(2)), int(m.group(3))
    elif m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9}),?\s+(\d{4})", text):
        mo = MONTHS.get(m.group(2).lower(), 0) or {k[:3]: v for k, v in MONTHS.items()}.get(m.group(2).lower()[:3], 0)
        d, y = int(m.group(1)), int(m.group(3))
    else:
        return None
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def norm_number(value) -> float | None:
    """A float from 8500, '8,500.00', '$8,500' or '150%'; None if there is no number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,$%\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


def norm_text(value) -> str:
    return " ".join(re.sub(r"[^0-9a-z ]", " ", str(value).casefold()).split())


def norm_section(value) -> str:
    """'Section 11', 'Article 11.' and '11. Renewal' all become '11'."""
    m = re.search(r"\d+(?:\.\d+)*", str(value))
    return m.group(0) if m else norm_text(value)


def fuzzy_match(want, got, threshold: int = FUZZY_THRESHOLD) -> bool:
    return fuzz.token_set_ratio(norm_text(want), norm_text(got)) >= threshold


def field_correct(name: str, want, got) -> bool:
    """Normalized exact match for dates and numbers, fuzzy (>= 85) for names. A null expectation is met only by null."""
    base = name.rsplit(".", 1)[-1]
    if want is None:
        return got is None
    if got is None:
        return False
    if base in DATE_FIELDS:
        return norm_date(want) is not None and norm_date(want) == norm_date(got)
    if base in NUMBER_FIELDS:
        a, b = norm_number(want), norm_number(got)
        return a is not None and b is not None and math.isclose(a, b, abs_tol=0.005)
    if base in NAME_FIELDS:
        return fuzzy_match(want, got)
    if base in CONTAINS_FIELDS:
        return all(norm_text(w) in norm_text(got) for w in want)
    if base == "notice_section":
        return norm_section(want) == norm_section(got)
    return norm_text(want) == norm_text(got)


# ---- span scoring (CUAD) ----

def tokens(text: str) -> list[str]:
    return re.findall(r"[0-9a-z]+", (text or "").casefold())


def span_prf(pred: str | None, gold_spans: list[str]) -> tuple[float, float, float]:
    """Token-level precision, recall and F1 of a predicted quote against the union of the labeled spans (bag of tokens)."""
    p, g = Counter(tokens(pred or "")), Counter(t for span in gold_spans for t in tokens(span))
    overlap = sum((p & g).values())
    if not p or not g or not overlap:
        return 0.0, 0.0, 0.0
    precision, recall = overlap / sum(p.values()), overlap / sum(g.values())
    return precision, recall, 2 * precision * recall / (precision + recall)


# ---- calibration ----

def bucket_of(confidence: float) -> str:
    if confidence < 0.7:
        return BUCKETS[0]
    return BUCKETS[1] if confidence < 0.9 else BUCKETS[2]


def calibration(items: list[tuple[float, bool]]) -> dict:
    """Accuracy and mean confidence per confidence bucket, plus expected calibration error over those buckets."""
    grouped: dict[str, list[tuple[float, bool]]] = {b: [] for b in BUCKETS}
    for confidence, correct in items:
        grouped[bucket_of(confidence)].append((confidence, correct))
    rows, total, error = {}, len(items), 0.0
    for bucket, members in grouped.items():
        n = len(members)
        accuracy = sum(c for _, c in members) / n if n else None
        mean_conf = sum(x for x, _ in members) / n if n else None
        rows[bucket] = {"n": n, "accuracy": accuracy, "mean_confidence": mean_conf}
        if n:
            error += n / total * abs(accuracy - mean_conf)
    return {"buckets": rows, "ece": error if total else None, "n": total}


def ece(items: list[tuple[float, bool]]) -> float | None:
    return calibration(items)["ece"]


# ---- descriptive stats ----

def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def p95(values: list[float]) -> float | None:
    """Nearest-rank 95th percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


# ---- baseline diff ----

def diff_baseline(current: dict[str, float], baseline: dict[str, float], points: float = REGRESSION_POINTS) -> list[dict]:
    """Metrics (rates in 0..1) that dropped by more than `points` percentage points. Keys missing on either side are skipped."""
    drops = []
    for key, old in sorted(baseline.items()):
        new = current.get(key)
        if new is None or old is None:
            continue
        delta = (new - old) * 100
        if delta < -points:
            drops.append({"metric": key, "baseline": old, "current": new, "delta_points": round(delta, 2)})
    return drops
