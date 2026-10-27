# Held-out rent rolls

Put here, for each rent roll, two files with the same stem:

| File | What it is |
|---|---|
| `<stem>.pdf` | Only the rent roll pages of the offering memorandum (make it with `scripts/split_pdf.py`). |
| `<stem>.json` | Your label, started from `evals/label_templates/rent_roll.template.json`. |

What the label holds (all of it written by hand, from the PDF):

- `headers` and `mapping`: the column headers as printed, and which column is the unit, tenant, area, lease dates, rents and $/SF.
- `rows`: one entry per unit row (no totals or subtotal rows) with its PDF page and every mapped cell.
- `totals`: the stated totals row, and `expected_flags` (`["totals_mismatch"]` only if you checked the rows and the stated total really differ).
- `metrics` (optional): occupancy, total annual rent, average rent per sf, WALT, expirations, worked out by you as of `as_of`.

Check it with `python scripts/check_labels.py`. Everything here except this README is git-ignored. Do not tune against these
documents; see `../README.md`.
