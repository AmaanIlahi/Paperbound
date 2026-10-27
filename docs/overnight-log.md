# Overnight log: CRE pack (rent roll, T-12, cross-check)

Branch: `feature/cre-pack`. Started from a clean `main` (496 tests passing at the start).
Rules followed: no server started, nothing merged or deployed, no Fly or Supabase access, `evals/heldout/` never opened
(only the `--heldout` flag is allowed to read it), temperature 0 and `--budget 500` for every eval run.

## Decisions, assumptions and things I would normally have asked

(Each entry is dated by phase. Where a choice was ambiguous I took the conservative option.)

### Phase 1: rent roll

- **Tables come from PyMuPDF geometry, text from page words.** `find_tables` finds the rows and column edges, but its own cell text
  is corrupt on borderless tables (digits split across lines, spaces lost). So cell text is rebuilt from the page's words by cell
  position. Continuation pages are joined to the first table when the repeated header reads the same, and their rows are
  re-binned into the first table's column edges (pages can detect different columns). A row that is a single spanning cell
  (title, footnote) is kept as a note, not a row.
- **The PDF bytes reach the extractor.** Stored pages carry words but not the file, so the worker reads the PDF back from storage
  (`jobs.load_pdf`). If the file is not there (the known volume/database split), the run reports "tables cannot be detected" and
  asks for review; it does not fall back to guessing from words. `run_pipeline(..., pdf=...)` is the new parameter.
- **A cell must be only a number to count as one.** The shared `amounts_in` happily pulls `2` out of "TBD-ish 2k". For table cells
  I require the cell to be a number plus decoration (`$`, `SF`, `/yr`, brackets) and nothing else; otherwise `cell_unparsed`
  (blocking). Two-digit-year dates (`11/28/19`) are read as 20YY in rent-roll cells only. "MTM"/month-to-month is understood.
- **Table-level problems: where the flag lands.** `header_mapping_suspect` is put on the `mapping` field and on every row (the
  meaning of every value is in doubt, so nothing feeds a metric or action). `totals_mismatch` is put only on the `totals` field:
  the rows may all be right and only the stated total wrong, and one bad row cannot be told from another. Metrics that use sums
  still show the "totals do not add up" line, and the review flag is raised. Clean rows still feed reminders. **Conservative
  alternative if you disagree:** flag every row on a totals mismatch (then the CSV and reminders are empty for that document).
- **Empty column is not "suspect".** A column mapped to sqft or rent with no non-blank cells has nothing to misread; its metrics are
  simply absent. Only a column whose non-blank cells are less than 70% numeric is suspect.
- **Reminder shape.** "Reminders for leases expiring within 12 and 24 months" is implemented as one calendar event per usable
  lease on its end date, in two bundle groups (`lease_expirations_12m`, `lease_expirations_24m`), capped at the 25 soonest
  (noted on the step). A review flag is proposed only when a check failed or nothing could be read. No email, no webhook, no tracker.
- **WALT** = sum(weight x years left) / sum(weight), years left = max(0, end - today) / 365.25; weights are annual rent or sqft;
  month-to-month and already-expired occupied leases count as 0 years. Occupied rows without the needed figure are left out
  and the note says how many.
- **Annual rent used** per row: stated annual, else monthly x 12, else sqft x $/sf (annualised if the $/sf header says monthly);
  the source is stored with the row.
- **Classifier:** `rent_roll` and `t12` joined the heuristic table (whole-word regexes, so "sf" cannot match inside a word) and the
  LLM enum. Existing fixtures score 0 on both new types. The existing assertion on the exact type set was updated.
- **UI:** Rows tab (shown only for a rent roll), CSV link, metrics in Summary as the usual cards plus an "All numbers" list;
  `tests/test_ui_links.py` now expects the four always-visible tabs plus the hidden table tabs.
- **CSV:** clean rows only; cells starting with `= + - @` get a leading apostrophe; a `table_warning` column carries `totals_mismatch`.

### Phase 2: T-12 operating statement

- **Columns by code, labels by the model.** Month columns are any header that reads as a month (`Oct-25`, `Oct 2025`, `10/2025`,
  `2025-10`, `October`); month names must be exact ("Marketing" is not March). The total column is the first non-month header
  that reads "Total", "T-12", "TTM", "Annual"... A statement needs exactly 12 month columns, else it is "not found" and goes to
  review (no guessing with 11 or 13). The model returns only `line number -> category`; a line it does not categorise (or one with
  a category outside the list) is flagged `category_suspect`, not dropped.
- **Sign conventions are code, not the model:** a vacancy/credit loss always reduces income whichever sign is printed
  (`-abs`), expenses enter as `abs`, other revenue keeps its sign, below-the-line items (capex, reserves, debt service,
  depreciation) are excluded from NOI. A negative expense line (a refund) will therefore show up as a mismatch against a stated
  total: conservative, a human looks.
- **Which subtotals are checked.** Only the ones whose composition is known: EGI ("effective gross", EGI), a "Total
  Income/Revenue" line (must equal gross income OR net-of-vacancy income), "Total Operating Expenses", and NOI. Section subtotals
  such as "Total Utilities" or "Cash Flow After Reserves" are shown but cannot be verified, so they are not flagged.
- **Tolerance.** Rounded statements make months and totals differ by a few dollars. The tolerance is `6.5 x` the statement's rounding
  unit (1000, 100, 10, 1, or 0.01 if any figure has cents; minimum $1), never a share of the figure: a first version used 0.1%
  and let a $900 error on a $1.2M line through; the test caught it.
- **Metrics use only lines without a blocking flag**, and the note says how many flagged lines were left out and which. The
  *checks* (line sums, subtotals, NOI) use every categorised line as printed.
- **Anomalies:** a month more than 25% from the line's median (median of the non-blank months, at least 6 of them, median not
  zero) gets `month_anomaly`. It is informational, not blocking, so the line still counts; it does raise the review flag, as
  asked. Lines that are naturally lumpy (annual premium paid in one month) will be flagged every time: expected.
- **Obligations:** none (a T-12 states no dates). The planner proposes a review flag when any check failed or a month looks off,
  and nothing otherwise. Section headings above a line (INCOME, OPERATING EXPENSES) are passed to the labeller as context only.
- **Brief card text** is short (`$840,000.00`, `35.1%`) with the explanation in the note, so cards fit.
- **Fixtures:** ten T-12s in three layouts; plants: one stated-NOI mismatch (t12_006), one capex inside the operating expenses
  with stated totals that include it (t12_008), one line whose months do not add to its total (t12_007), two month spikes
  (t12_002, t12_009), one base rent 18% below its rent roll (t12_004). The first five share a property and in-place rent with
  rent rolls 1-5, which is what the Phase 3 pairs use. Fixture truth stores months exactly as printed (a loss printed
  positive stays positive in the truth); the metrics are computed from the true values.
- **Classifier bug found and fixed:** 43 `\b` word boundaries in the new classifier regexes had become backspace characters
  through a bad edit, so the T-12 patterns matched nothing for two layouts. After the fix every dev fixture of every type
  (and every lease, certificate and generic fixture) classifies by heuristics with the required margin, and none of the
  generic fixtures is claimed by a new type.

### Phase 3: rent roll <-> T-12 cross-check

- **Where the property comes from.** Each of the two extractions now also stores a `property` field (name and street address) read by
  code from the first lines of page 1: the address is the first line that looks like a street address or ends in `ST 12345`,
  the name is the line just above it, else the first plausible title line (document-title words, footers and column headers are
  skipped; "Name - Rent Roll" is cut at the dash). All 20 dev documents read back exactly. No model. If nothing is found the
  field is absent and the cross-check says identity "could not be compared"; it never guesses.
- **Matching.** Names: rapidfuzz `token_set_ratio` >= 85 after dropping words like LLC, "the", "center", "properties".
  Addresses: `token_sort_ratio` >= 85 after expanding street abbreviations (st, ave, blvd, n, ...). Either one failing is a
  `property_mismatch` **warning** (never blocking). I could not find a principled way to pick these thresholds without
  real documents; they are constants in `pipeline/property.py` for you to tune on the held-out set.
- **Rent income.** Rent roll `total_annual_rent` (the usable rows' sum, same as the Summary) against the T-12's `base_rent`
  lines (usable ones only, months summed). Difference = T-12 minus rent roll; share = difference / rent roll. Default tolerance
  5% (`CROSSCHECK_RENT_TOLERANCE` env, or `tolerance` in the request, 0 < t <= 1). Over the tolerance is `rent_income_mismatch`.
  A rent roll whose rows do not add to its totals still compares (on its usable rows) and says so in the note.
- **Vacancy is one-sided.** The T-12's base rent is in-place rent, so vacant space does not show up as a loss: a T-12 that
  shows *less* vacancy loss than the rent roll's vacancy is normal. It is a `vacancy_sanity` warning only when the T-12's
  vacancy and credit loss, as a share of its gross income, exceed the rent roll's vacant share (area, else units) by more than
  ten points. Warning only, never changes the status beyond "review".
- **Report status:** `ok` (nothing to say), `review` (any flag, warning or mismatch), `incomplete` (nothing flagged but a
  comparison was unavailable). Unavailable is used when a side has no usable figure; nothing is estimated.
- **API.** `POST /crosscheck` `{rent_roll_run_id, t12_run_id, tolerance?}`. 404 for runs that are not the caller's (same as
  nonexistent), 422 for the wrong document type (`Run 3 is a t12, not a rent roll`) or a bad tolerance, 409 if a run is not
  `done` (I read "completed" as `done`; a `partial` run is refused). Nothing is stored: the report is recomputed from the
  stored fields each time, so there is no new table and no migration. I also added `GET /runs` (the caller's own runs, newest
  first, optional `doc_type`) because the Compare view needs something to pick from.
- **UI.** A "Compare" link in the header and a `#compare` view: two selects (completed runs only), a tolerance box (percent), the
  report as cards with both sides, "Source: page N" links that open the run at that page, and the difference.

### Phase 4: dev fixtures and evals

- **BLOCKER: the OpenAI account has no credits.** The first real eval run returned `429 insufficient_quota` ("You have no credits
  remaining") for all 20 calls. So **no real-model eval result exists for any suite tonight**: not for the new rent roll, T-12
  and cross-check suites, and not the re-run of lease, certificate, generic and CUAD. That run also wrote six junk result files
  (all zeros) and overwrote `latest.md`; I deleted them and restored `latest.md`, then added a guard so it cannot happen again:
  a suite where every document errored is reported as `NOT SAVED`, writes nothing, leaves the baseline alone and exits 2.
  (The 20 failed calls are in the local, git-ignored `evals/.budget.json` ledger; they cost nothing.)
- **What I could verify offline instead** (none of this replaces the real runs):
  1. *Oracle run.* With each fixture's true column mapping and true label categories standing in for the model, the whole eval path
     scores: rent roll header mapping 100%, cells 100% (1,880 cells), row exact 100%, totals check 100% (planted one found, no
     false alarm), metrics 100%, actions 100%; T-12 line category 100%, cells 100%, NOI exact 100%, mismatch recall/precision 100%
     (4 planted mismatches), anomaly recall/precision 100%, usable-view category accuracy 97.5% (the 4 flagged lines are abstained
     by design); cross-check 5/5 pairs right, planted rent income mismatch found, 5/5 negative controls give a property mismatch.
     **This is the ceiling of the code with a perfect model, not a model result.** It is what tests/test_cre_evals.py asserts.
  2. *Classification parity.* On all 47 existing documents (20 leases, 20 certificates, the demo PDFs and the 5 generic fixtures)
     the heuristic classifier makes the identical decision on `main` and on this branch (21 lease, 21 certificate, 5 left to the LLM).
  3. *Scoring parity.* Re-deriving the summaries of the newest saved synthetic, generic and CUAD runs with the current scoring code
     reproduces them exactly, and every one of the 160 baseline keys is still produced.
  4. *Open risk I cannot close offline:* the classifier's LLM prompt now offers `rent_roll` and `t12` too. It is only used when the
     heuristics are unsure (the 5 generic fixtures). A real run should confirm none of them is claimed by a new type.
- **Bug the scorer found:** `run_pipeline` called a table type's obligations step without the run's `today`, so a run "as of
  2026-10-22" counted from the system date (one lease ending 2026-10-25 vanished). Fixed (`spec.obligations(validated, today)` for
  table types) with a regression test.
- **Metrics (what each suite reports, all with a usable view beside it).** Rent roll: header-mapping accuracy (per column, and
  documents with every column right), cell accuracy (every mapped cell of every row), row exact, page accuracy, totals check
  correct (flagged exactly when planted), metric accuracy (occupancy by units and area, total rent, average rent per sf, WALT by
  rent and area, expirations by year, leases expiring within 24 months), action correctness, per-layout breakdown. T-12: month
  headers, line category accuracy, cell accuracy as printed, NOI exact match, the other figures, mismatch detection recall/precision
  (line sums, subtotals, NOI, capex), anomaly recall/precision. Cross-check: planted mismatch found, false alarms, same property
  recognised, different property found. Usable view: a cell/line carrying a blocking flag is abstained.
- **Reuse of runs:** the cross-check suite reuses the rent roll and T-12 runs of the same fixtures in-process (no extra model calls);
  `--suite all` is the 5 old suites plus these three. Expected cost of the new suites: 20 mapping calls.
- **Baseline:** I did not write anything to `evals/baseline.json` (no real result exists). `--merge-median-baseline <runs>` adds the
  median of saved runs for the new suites only and leaves every old key untouched; see the morning list.
- **Planted problems in the dev set:** rent roll rr_003 (stated annual rent total off by $12,000); T-12 t12_006 (stated NOI +$1,500 a
  month), t12_007 (insurance total +$900), t12_008 (capex inside operating expenses, stated totals include it), t12_002 / t12_009
  (a month ~90% above its line), and pair 4 (T-12 base rent 18% under the rent roll). Layouts: rent roll A (ruled grid, MM/DD/YYYY,
  monthly + annual + $/SF), B (ruled grid, tenant first, $/SF only, status column, "Mon D, YYYY"), C (borderless, ISO dates, no
  currency signs, up to three pages with a repeated header), D (banded, two-digit years, monthly rent only, wrapped headers).

### Phase 5: held-out scaffolding

- **I never opened, read or listed anything in `evals/heldout/`** other than writing the four README files into it. The tests build
  their own held-out folder from the dev fixtures in a temp directory (it has to be named `heldout`, which is how the loader
  recognises the flat layout), and the `.gitignore` test asks `git check-ignore` about paths that need not exist.
- **Layout.** Held-out documents are flat: `evals/heldout/rent_roll/<stem>.pdf` + `<stem>.json`, `evals/heldout/t12/<stem>.pdf` +
  `<stem>.json`, `evals/heldout/pairs/<stem>.json` (a pair names the two stems). The dev set keeps its folder-per-fixture layout;
  both are loaded through one `Fixture` type so the same scorers read both. A label may carry its own `as_of`; the run counts
  each document from it, so hand-computed WALT and expirations agree.
- **`.gitignore`:** `evals/heldout/**` with the four README files re-included, and `evals/results/heldout_*` plus
  `evals/results/failures_heldout_*` (held-out results describe private documents). Checked with `git check-ignore`.
- **Templates** are in `evals/label_templates/` (committed, outside the ignored folder): `rent_roll.template.json`,
  `t12.template.json`, `pair.template.json`. They are blank. JSON has no comments, so every field `x` has a sibling `_x` with
  its explanation, plus a worked `_example_row` / `_example_line`; keys starting with an underscore are ignored by the validator.
  Each says to label by hand and not to copy Paperbound's output.
- **`scripts/check_labels.py`** validates label files (all of them under `evals/heldout`, or the ones named; `--root` to point
  elsewhere): missing fields, wrong types, values outside the allowed lists, a column number past the last header, a category that
  is not in the list, 12 months per line, a pair that names files that do not exist, `difference` that is not T-12 minus rent roll.
  A blank template fails with "is empty" for each required field. Missing *optional* labels (metrics, totals, expected figures)
  are notes, not errors: the eval then skips that metric for that document. Exit code 1 if any file has an error.
- **`scripts/split_pdf.py`** (`--pages 14-16`, `3,5,9-11`, order kept; 1-based; refuses a page that does not exist, a backwards
  range, the input as output, or an existing output without `--force`; never changes the input).
- **`--heldout`** runs only rent roll, T-12 and cross-check over `evals/heldout`. It first validates every label and refuses to
  run (exit 2, no model call) if any is incomplete; with nothing in the folder it says so and exits 0. It refuses `--save-baseline`,
  `--median-baseline`, `--merge-median-baseline` and `--rescore`, does no baseline comparison, writes
  `evals/results/heldout_<suite>_<ts>.json` and `heldout_latest.md` (ignored by git), does not rebuild `latest.md`, and `/evals`
  cannot show it. It prints a reminder not to change code in response. The same all-documents-failed guard applies.
- **Label shape = scorer shape.** The held-out labels use the same keys the scorers read from the dev `expected.json`
  (`mapping`, `rows[].cells`, `totals`, `expected_flags`, `metrics` / `lines`, `expected_flags`, `expected_anomalies`, `expected`).
  One thing I changed to make that true: the rent roll scorer now reads `expected_flags` (it used to read the dev-only `planted`).

