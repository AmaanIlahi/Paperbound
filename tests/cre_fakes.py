"""Helpers for the tabular document tests: hand-built tables (no PDF), a fake column or label mapper, and a fake client."""

from types import SimpleNamespace

from paperbound.pipeline import rent_roll as rr
from paperbound.pipeline import tables as tables_mod
from paperbound.pipeline.mapping import MappingResult

PAGES = [SimpleNamespace(page_no=1, quality_score=1.0), SimpleNamespace(page_no=2, quality_score=1.0)]
HEADER = ["Suite", "Tenant", "Sq Ft", "Start", "End", "Monthly", "Annual", "$/SF"]
MAPPING = {0: "unit", 1: "tenant", 2: "sqft", 3: "lease_start", 4: "lease_end", 5: "monthly_rent", 6: "annual_rent", 7: "rent_psf"}
GOOD_ROWS = [
    ["100", "Acme Dental", "2,000", "01/01/2024", "12/31/2028", "$5,000.00", "$60,000.00", "$30.00"],
    ["110", "Blue Cafe", "1,000", "03/01/2023", "02/28/2027", "$2,500.00", "$30,000.00", "$30.00"],
    ["120", "VACANT", "1,500", "", "", "", "", ""],
    ["Total", "", "4,500", "", "", "$7,500.00", "$90,000.00", ""],
]


def make_table(header=None, rows=None, page=1) -> tables_mod.Table:
    header = header or HEADER
    rows = GOOD_ROWS if rows is None else rows
    return tables_mod.Table(
        list(header), 1, [0.0, 0.0, 100.0, 10.0], [float(i * 10) for i in range(len(header) + 1)],
        [tables_mod.TableRow(list(cells), page, [0.0, 10.0 * (i + 1), 100.0, 10.0 * (i + 1) + 8]) for i, cells in enumerate(rows)],
        pages=[page],
    )


def extraction(rows=None, header=None, mapping=None):
    table = make_table(header, rows)
    return rr.build_table(table, dict(MAPPING if mapping is None else mapping), [])


def validated(rows=None, header=None, mapping=None):
    return rr.validate_rent_roll(extraction(rows, header, mapping), PAGES)


def named(fields):
    return {f.name: f for f in fields}


def fake_mapper(mapping):
    return lambda header, rows: MappingResult(dict(mapping), "gpt-4o-mini", 100, 0.0001)


class FakeParseClient:
    """Stands in for the OpenAI client: records the request and answers with a prepared parsed object."""

    def __init__(self, parsed):
        self.requests = []
        usage = SimpleNamespace(total_tokens=300, prompt_tokens=280, completion_tokens=20)
        self.reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.requests.append(kwargs)
        return self.reply


# ---- T-12 ----

from paperbound.pipeline import t12 as t12_mod  # noqa: E402

MONTHS12 = ["Oct-25", "Nov-25", "Dec-25", "Jan-26", "Feb-26", "Mar-26", "Apr-26", "May-26", "Jun-26", "Jul-26", "Aug-26", "Sep-26"]
T12_HEADER = ["Account", *MONTHS12, "Total"]


def money_cell(v) -> str:
    return f"({abs(v):,.0f})" if v < 0 else f"{v:,.0f}"


def t12_row(label, values, total=None):
    """A statement row: label, twelve monthly figures (a number repeats for every month), the total column."""
    values = [values] * 12 if isinstance(values, (int, float)) else list(values)
    return [label, *[money_cell(v) for v in values], money_cell(sum(values) if total is None else total)]


# category of each label in the standard statement below; the real model would return these
CATEGORIES = {
    "Rent": "base_rent", "Reimbursements": "recoveries", "Other Income": "other_income", "Vacancy": "vacancy_credit_loss",
    "Effective Gross Income": "subtotal", "Real Estate Taxes": "taxes", "Insurance": "insurance", "Utilities": "utilities",
    "Repairs": "repairs_maintenance", "Management": "management", "Payroll": "payroll", "Admin": "general_admin", "Other Opex": "other_opex",
    "Total Operating Expenses": "subtotal", "Net Operating Income": "subtotal", "Capital Expenditures": "capex", "Debt Service": "debt_service",
}


def standard_rows(**over):
    """Rent 100k, recoveries 20k, other 5k, vacancy -5k = EGI 120k; opex 50k; NOI 70k, every month."""
    rows = {
        "Rent": 100000, "Reimbursements": 20000, "Other Income": 5000, "Vacancy": -5000, "Effective Gross Income": 120000,
        "Real Estate Taxes": 15000, "Insurance": 5000, "Utilities": 8000, "Repairs": 6000, "Management": 4000, "Payroll": 7000, "Admin": 3000,
        "Other Opex": 2000, "Total Operating Expenses": 50000, "Net Operating Income": 70000,
    }
    rows.update(over)
    return [t12_row(label, value) for label, value in rows.items() if value is not None]


def t12_extraction(rows=None, categories=None, notes=None):
    rows = standard_rows() if rows is None else rows
    table = make_table(T12_HEADER, rows)
    if notes:
        table.notes, table.note_at = [n for n, _ in notes], [at for _, at in notes]
    cols = list(range(1, 13))
    lines = t12_mod.build_lines(table, cols, 13, 0)
    cats = CATEGORIES if categories is None else categories
    for ln in lines:
        ln.category = cats.get(ln.label)
    return t12_mod.T12Extraction(True, months=MONTHS12, month_cols=cols, total_col=13, label_col=0, header=T12_HEADER, header_page=1,
                                 header_bbox=[0.0, 0.0, 100.0, 10.0], lines=lines, pages=[1])


def t12_validated(rows=None, categories=None, notes=None):
    return t12_mod.validate_t12(t12_extraction(rows, categories, notes), PAGES)


# ---- the true mapping standing in for the model, for the eval tests ----

def install_oracle(monkeypatch, current: dict | None = None) -> dict:
    """Replace the two mapping calls by the true mapping of whichever fixture is being run (read from its label), and return a dict
    the test can use to spoil it: current['spoil'] = 'swap_size_and_rent' | 'utilities_as_taxes'."""
    import json

    from paperbound.evals import cre

    cre.clear_cache()
    current = {} if current is None else current
    original = cre.run_document

    def run_document(fixture, as_of, budget=None):
        exp = json.loads(fixture.truth.read_text(encoding="utf-8"))
        if fixture.kind == "rent_roll":
            current["rr"] = exp["mapping"]
        else:
            current["t12"] = {ln["label"]: ln["category"] for ln in exp["lines"]}
        current["id"] = exp["id"]
        return original(fixture, as_of, budget)

    def map_columns(header, rows, client=None):
        mapping = {int(k): v for k, v in current["rr"].items()}
        if current.get("spoil") == "swap_size_and_rent" and current["id"] == "rr_001":
            mapping[2], mapping[6] = mapping[6], mapping[2]
        return MappingResult(mapping, "gpt-4o-mini", 100, 0.0001)

    def map_labels(labels, client=None):
        cats = dict(current["t12"])
        if current.get("spoil") == "utilities_as_taxes":
            cats = {k: ("taxes" if v == "utilities" else v) for k, v in cats.items()}
        return MappingResult({i: cats.get(label) for i, (label, _) in enumerate(labels)}, "gpt-4o-mini", 90, 0.0001)

    monkeypatch.setattr(cre, "run_document", run_document)
    monkeypatch.setattr(rr, "map_rent_roll_columns", map_columns)
    monkeypatch.setattr(t12_mod, "map_t12_labels", map_labels)
    return current
