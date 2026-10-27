"""Copy a page range of a PDF into a new PDF (PyMuPDF), e.g. the rent roll pages of an offering memorandum.

    python scripts/split_pdf.py om.pdf --pages 14-16 -o evals/heldout/rent_roll/prop_a.pdf
    python scripts/split_pdf.py om.pdf --pages 3,5,9-11 -o out.pdf

Pages are numbered from 1, as a PDF viewer shows them. A range is written FIRST-LAST; a list is comma separated and keeps
its order (3,1 writes page 3 then page 1). The input is never changed, and an existing output file is not overwritten
unless --force is given.
"""

import argparse
import sys
from pathlib import Path

import pymupdf


def parse_pages(spec: str, page_count: int) -> list[int]:
    """0-based page indexes for a spec such as '14-16' or '1,3,5-7'. Raises ValueError with a plain message."""
    if not spec or not spec.strip():
        raise ValueError("no pages given")
    out: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            raise ValueError(f"empty item in {spec!r}")
        if "-" in part:
            first, _, last = part.partition("-")
            if not (first.isdigit() and last.isdigit()):
                raise ValueError(f"{part!r} is not a page range like 14-16")
            a, b = int(first), int(last)
            if a > b:
                raise ValueError(f"{part!r} runs backwards; write it as {b}-{a}")
            numbers = list(range(a, b + 1))
        elif part.isdigit():
            numbers = [int(part)]
        else:
            raise ValueError(f"{part!r} is not a page number")
        for n in numbers:
            if not 1 <= n <= page_count:
                raise ValueError(f"page {n} does not exist: the PDF has {page_count} page(s)")
            out.append(n - 1)
    return out


def split_pdf(source: Path, spec: str, target: Path, force: bool = False) -> int:
    """Write the pages of `spec` from `source` to `target`. Returns the number of pages written."""
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise ValueError(f"{source} does not exist")
    if source.resolve() == target.resolve():
        raise ValueError("the output must be a different file from the input")
    if target.exists() and not force:
        raise ValueError(f"{target} already exists; pass --force to overwrite it")
    try:
        doc = pymupdf.open(source)
    except Exception as exc:
        raise ValueError(f"cannot open {source} as a PDF: {exc}") from exc
    with doc:
        if doc.needs_pass:
            raise ValueError(f"{source} is password protected")
        indexes = parse_pages(spec, len(doc))
        out = pymupdf.open()
        try:
            for index in indexes:
                out.insert_pdf(doc, from_page=index, to_page=index)
            target.parent.mkdir(parents=True, exist_ok=True)
            out.save(target, garbage=3, deflate=True)
        finally:
            out.close()
    return len(indexes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path, help="the PDF to take pages from")
    parser.add_argument("--pages", required=True, help="pages to keep, numbered from 1: 14-16 or 3,5,9-11")
    parser.add_argument("-o", "--output", required=True, type=Path, help="the new PDF to write")
    parser.add_argument("--force", action="store_true", help="overwrite the output if it exists")
    args = parser.parse_args(argv)
    try:
        n = split_pdf(args.pdf, args.pages, args.output, args.force)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {n} page(s) of {args.pdf} to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
