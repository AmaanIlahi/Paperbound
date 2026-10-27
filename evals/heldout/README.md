# Held-out evals

This folder is for **real documents** (pages cut from offering memoranda) with labels you write by hand. Everything in it except
the README files is git-ignored: the documents are not ours to publish and the labels describe them.

The dev fixtures in `evals/fixtures/` are template-generated and were used while building the rent roll and T-12 code, so they
only show that the code works on documents like the ones it was written against. **This set is the honest measure.** The rules:

1. Never tune against it. Do not change a prompt, a regex, a threshold or a layout rule in response to a held-out result. If a
   held-out document shows a real bug, write it down, then reproduce it on a new *dev* fixture and fix it there.
2. It never updates `evals/baseline.json`. `python -m paperbound.evals.run --heldout` refuses `--save-baseline` and the other
   baseline options, saves its results as `evals/results/heldout_*` (git-ignored), and `/evals` does not show them.
3. Do not fill labels from Paperbound's output. A label is what the document says, read by a person. A label copied from the model
   would score the model against itself.

## Layout

```
evals/heldout/
  rent_roll/  <stem>.pdf  <stem>.json     a rent roll (only the rent roll pages) and its label
  t12/        <stem>.pdf  <stem>.json     a T-12 operating statement and its label
  pairs/                  <stem>.json     a rent roll + T-12 of one property (labels only; the PDFs are in the two folders above)
```

## Steps

1. Cut the pages out of an offering memorandum:
   `python scripts/split_pdf.py om.pdf --pages 14-16 -o evals/heldout/rent_roll/prop_a.pdf`
2. Copy the blank from `evals/label_templates/` (`rent_roll.template.json`, `t12.template.json`, `pair.template.json`) to the
   `.json` next to the PDF and fill it in. Every field is explained in the template.
3. Check it: `python scripts/check_labels.py` lists what is missing or wrong, file by file.
4. Run it: `python -m paperbound.evals.run --heldout --budget 500` (about one model call per document).
