# Held-out T-12 operating statements

Put here, for each statement, two files with the same stem:

| File | What it is |
|---|---|
| `<stem>.pdf` | Only the operating statement pages (make it with `scripts/split_pdf.py`). |
| `<stem>.json` | Your label, started from `evals/label_templates/t12.template.json`. |

What the label holds (all of it written by hand, from the PDF):

- `months` and `total_header`: the twelve month headers and the total column header as printed.
- `lines`: every row with figures, subtotals included, with its label as printed, its category (what the line *is*), its twelve
  figures and total as printed, and its PDF page.
- `expected_flags`: problems really in the statement (a stated NOI or subtotal that does not follow from the lines, a line whose
  months do not add to its total, capex counted among the operating expenses), checked by you with a calculator.
- `expected_anomalies`: months more than 25% from their line's median.
- `expected` (optional): EGI, operating expenses, NOI, expense ratio, monthly NOI and expenses by category, worked out by you.

Check it with `python scripts/check_labels.py`. Everything here except this README is git-ignored. Do not tune against these
documents; see `../README.md`.
