"""Check the held-out label files against the schema and report what is missing.

    python scripts/check_labels.py                     # every label under evals/heldout
    python scripts/check_labels.py evals/heldout/t12/prop_a.json
    python scripts/check_labels.py --root some/other/folder

A label file is <stem>.json next to <stem>.pdf in evals/heldout/rent_roll/ or evals/heldout/t12/, or <stem>.json in
evals/heldout/pairs/. Start from the blanks in evals/label_templates/. Exit status 0 means every file is complete enough to
score; 1 means at least one has an error. Warnings (for example "metrics not labeled") do not fail the check: the eval
simply skips what is not labeled.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperbound.evals import labels  # noqa: E402

DEFAULT_ROOT = ROOT / "evals" / "heldout"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="label files to check (default: every label under the root)")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the held-out folder (default: evals/heldout)")
    args = parser.parse_args(argv)

    targets = [(None, p) for p in args.files] if args.files else labels.find_labels(args.root)
    if not targets:
        print(f"no label files found under {args.root} (expected rent_roll/<stem>.json, t12/<stem>.json, pairs/<stem>.json)")
        return 0
    failed = 0
    for kind, path in targets:
        report = labels.validate(path, kind)
        status = "OK   " if report.ok else "ERROR"
        print(f"{status} {path}")
        for message in report.errors:
            print(f"   missing or wrong  {message}")
        for message in report.warnings:
            print(f"   note              {message}")
        failed += not report.ok
    print(f"\n{len(targets) - failed} of {len(targets)} label file(s) are complete" + (f"; {failed} need fixing." if failed else "."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
