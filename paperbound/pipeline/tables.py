"""Table detection for the tabular document types (rent roll, T-12). Deterministic: no model.

PyMuPDF's `find_tables` finds where the tables are (rows and column edges); the text of each cell is taken from the
page's own words, assigned to a cell by the position of its centre. (`find_tables` builds cell text from characters and
on borderless tables it splits digits and drops spaces, so only its geometry is used.) A table that continues on the
next page is joined to the first one when the repeated header row reads the same, and its rows are re-assigned to the
first table's column edges, so a page whose detected columns differ still lines up. A row that is a single spanning
cell (a title or a footnote) is not a data row and is set aside as a note.

Every row keeps its page and its bounding box in PDF points.
"""

import re
from dataclasses import dataclass, field

import pymupdf

MIN_COLS = 3
HEADER_NUMERIC_SHARE = 0.3  # a header row has fewer numeric/date cells than this share of its non-empty cells
MAX_HEADER_ROWS = 3

_NUMERIC_CELL = re.compile(r"^[\s$€£(\-+]*\d[\d,]*(?:\.\d+)?[\s)%]*(?:sf|psf)?$", re.I)
_DATE_CELL = re.compile(r"^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}$|^[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}$")


@dataclass
class TableRow:
    cells: list[str]
    page_no: int
    bbox: list[float]


@dataclass
class Table:
    header: list[str]
    header_page: int
    header_bbox: list[float]
    col_edges: list[float]
    rows: list[TableRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # single-cell rows (titles, footnotes, section headings) set aside
    note_at: list[int] = field(default_factory=list)  # how many data rows came before each note (a heading's position)
    pages: list[int] = field(default_factory=list)

    @property
    def ncols(self) -> int:
        return len(self.header)

    @property
    def key(self) -> str:
        return header_key(self.header)


def header_key(cells: list[str]) -> str:
    return re.sub(r"[^a-z0-9$%/]", "", " ".join(cells).casefold())


def clean(text: str) -> str:
    return " ".join((text or "").split())


def is_data_cell(text: str) -> bool:
    text = clean(text)
    return bool(_NUMERIC_CELL.match(text) or _DATE_CELL.match(text))


def looks_like_header(cells: list[str]) -> bool:
    filled = [c for c in cells if clean(c)]
    if len(filled) < 2:
        return False
    return sum(is_data_cell(c) for c in filled) / len(filled) < HEADER_NUMERIC_SHARE


def _text_in(words, x0: float, y0: float, x1: float, y1: float) -> str:
    inside = [w for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
    inside.sort(key=lambda w: (w[5], w[6], w[7]))
    return clean(" ".join(w[4] for w in inside))


def _edges_of(row_cells) -> list[float]:
    boxes = [c for c in row_cells if c is not None]
    return [b[0] for b in boxes] + [boxes[-1][2]]


def _rebin(words, bbox, edges: list[float]) -> list[str]:
    """Cell texts of one row for the given column edges: each word goes to the column holding its centre."""
    x0, y0, x1, y1 = bbox
    n = len(edges) - 1
    cols: list[list] = [[] for _ in range(n)]
    for w in words:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if not (y0 <= cy <= y1):
            continue
        index = max((i for i in range(n) if cx >= edges[i]), default=0)
        cols[index].append(w)
    out = []
    for col in cols:
        col.sort(key=lambda w: (w[5], w[6], w[7]))
        out.append(clean(" ".join(w[4] for w in col)))
    return out


def _page_tables(page):
    """The tables of a page with at least MIN_COLS columns: ruled-line detection first, text alignment if that finds none."""
    for strategy in ("lines", "text"):
        try:
            found = [t for t in page.find_tables(strategy=strategy).tables if t.col_count >= MIN_COLS and t.row_count >= 2]
        except Exception:
            found = []
        if found:
            return found
    return []


def extract_tables(pdf: bytes) -> list[Table]:
    """Every logical table in the document, in order. Empty when the PDF has no text layer or no table."""
    tables: list[Table] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            page_no = page.number + 1
            words = page.get_text("words")
            if not words:
                continue
            for found in _page_tables(page):
                rows = list(found.rows)
                if not rows:
                    continue
                own = [[_text_in(words, *c, ) if c is not None else "" for c in r.cells] for r in rows]
                present = [sum(c is not None for c in r.cells) for r in rows]
                ncols = found.col_count
                # the first row that looks like a header opens (or repeats) the logical table
                head = next((i for i in range(min(MAX_HEADER_ROWS, len(rows))) if present[i] > 1 and looks_like_header(own[i])), None)
                master = None
                body_from = 0
                if head is not None:
                    parts = [own[head]]
                    j = head + 1
                    while j < len(rows) and len(parts) < MAX_HEADER_ROWS and present[j] > 1 and any(clean(c) for c in own[j]) \
                            and not any(re.search(r"\d", c) for c in own[j]) and looks_like_header(own[j]):
                        parts.append(own[j])
                        j += 1
                    combined = [clean(" ".join(p[k] for p in parts if k < len(p))) for k in range(len(own[head]))]
                    key = header_key(combined)
                    master = next((t for t in tables if t.key == key), None)
                    body_from = j
                    if master is None:
                        edge_row = max(range(len(rows)), key=lambda i: (present[i] == ncols, present[i]))
                        edges = _edges_of(rows[edge_row].cells)
                        if len(edges) - 1 != len(combined):
                            edges = _edges_of(rows[head].cells)
                        hb = rows[head].bbox
                        master = Table(combined, page_no, [round(v, 2) for v in hb], edges)
                        master.header_bbox = [round(min(r.bbox[0] for r in rows[head:j]), 2), round(rows[head].bbox[1], 2),
                                              round(max(r.bbox[2] for r in rows[head:j]), 2), round(rows[j - 1].bbox[3], 2)]
                        tables.append(master)
                elif tables:
                    master = tables[-1]  # a continuation page without a repeated header
                else:
                    continue
                if page_no not in master.pages:
                    master.pages.append(page_no)
                for i in range(body_from, len(rows)):
                    spanning = sum(1 for c in own[i] if clean(c)) <= 1 or present[i] <= 1
                    if spanning and ncols > 2:  # one spanning cell: a title or a footnote, not a data row
                        if clean(" ".join(own[i])):
                            master.notes.append(clean(" ".join(own[i])))
                            master.note_at.append(len(master.rows))
                        continue
                    cells = _rebin(words, rows[i].bbox, master.col_edges)
                    if not any(cells):
                        continue
                    master.rows.append(TableRow(cells, page_no, [round(v, 2) for v in rows[i].bbox]))
    return tables
