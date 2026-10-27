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
