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

