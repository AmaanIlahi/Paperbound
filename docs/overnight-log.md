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

