# Held-out pairs

A pair says that a held-out rent roll and a held-out T-12 are about the same property, and what the cross-check should find.
Put here one file per pair: `<stem>.json`, started from `evals/label_templates/pair.template.json`. There is no PDF in this folder:
the pair names the rent roll's stem (`rent_roll/<stem>.pdf` + `.json`) and the T-12's stem (`t12/<stem>.pdf` + `.json`), and both
must already exist.

What the label holds (all of it written by hand):

- `rent_roll` and `t12`: the stems of the two documents. `property`, `as_of` and `tolerance` (0.05 is 5%).
- `rent_roll_total_annual_rent`, `t12_base_rent`, `difference` and `difference_share`: worked out by you from the two documents.
- `expected_flags`: `[]` if they agree, `["rent_income_mismatch"]` if the difference is over the tolerance; `property_mismatch`
  or `vacancy_sanity` only if you can show it from the documents.

With two or more pairs the eval also builds negative controls (each rent roll against the next pair's T-12) and expects a
property mismatch from them. Check labels with `python scripts/check_labels.py`. Everything here except this README is git-ignored.
Do not tune against these documents; see `../README.md`.
