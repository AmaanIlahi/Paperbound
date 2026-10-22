# Report 3: Part 4 (decision brief, meaningful actions, source link fix)

Branch: `part-4` (from `part-3`; pushed, not merged; `main` untouched).

## Headline

- **Bug fixed**: every "because of" link now goes to its own page and highlights its own boxes. Root cause and test below.
- Everything in the brief is built. **159 pytest tests pass** (was 95), `alembic check` is clean.
- Final evals on all three fixtures: **14/14 fields, correct deadline, 6/6 brief numbers (all cite sources), 7/7 action checks** (3 reminders, 2 notice drafts, 1 tracker record). `rent_amount` stayed 8500 on both injection fixtures.
- OpenAI usage: **18 of 20 calls**, gpt-4o-mini only, about $0.014 in total.
- Four earlier eval runs each exposed a real problem (listed below). All fixed; the result files are kept.

## 1. The link bug

**Cause**: the "because of" button handler called `highlight(a.sources)` with the *whole list*, and `highlight` draws all of them and scrolls to the first one. So every link did the same thing: scroll to the first source (page 2, `end_date`). The first link looked fine; the page 3 link appeared to do nothing.

**Fix** ([web/index.html](web/index.html)): one link per source, each calling `highlight(thatSource)`. The same helper (`sourceLinks`) is used by the actions and the new brief card.

**Test**: `tests/ui/links.test.js` runs the real inline script of `index.html` in node on a small DOM stub, renders an action with three sources on pages 2, 3 and 2, clicks each link in a different order, and asserts the boxes drawn, their page, and the element scrolled to. I checked it fails against the old handler (it fails on link 1, the page 3 link). `tests/test_ui_links.py` runs it under pytest (skipped if node is missing). This tests the logic, not the look of the page: see "Needs your attention".

## 2. What was built

| Area | Files |
|---|---|
| New fields | `schemas/lease.py`: `holdover_rate_percent`, `security_deposit`, `notice_method`, `notice_section`, `current_rent`, plus `rent_escalation_percent` (see decisions). Same `{value, source_quote, page}` shape. `extract.py`: prompt rules and today's date in the user message |
| Validation | `validate.py`: quote rules, value-in-quote and range checks for the new fields (`invalid_percent` flag); rent-table row parser; `current_rent` cross-checked against the row covering today (`conflicting_values`); short quotes must match exactly; `notice_section` normalized by code |
| Decision brief | `pipeline/impact.py` (deterministic, no LLM), new `impact` pipeline step, `GET /runs/{id}/brief` |
| Planner | `agent/planner.py` rewritten around tokens: model writes prose, code supplies numbers, dates, names, reminder schedule and the facts block |
| Groups | Migration `0004` (`proposed_actions.group_key`, `group_mode`, both nullable), exclusive-approve logic in `routes.py` |
| UI | Decision Brief card, reminder group with "Approve all", "choose one" notice group, per-source links, `/#run=N&page=M` links that open a run |
| Tests/evals | 5 new test files, `test_planner.py` rewritten, `eval_lease.py` and `lease.expected.json` rewritten |
| Docs | `README.md` (results), `docs/architecture.md` (4.3, 4.4, new 4.5b, 4.6, limits, data model, API, UI) |

### The decision brief (lease, run on 2026-10-22)

| Line | Value | Cites |
|---|---|---|
| Notice deadline | December 31, 2026 | end_date, notice_period_days |
| Days remaining | 70 | end_date, notice_period_days |
| Current monthly rent | $9,017.65 | current_rent |
| Rent after one more 3% step | $9,288.18 (illustrative) | current_rent, rent_escalation_percent |
| Holdover rent (150%) | $13,526.48 / month | current_rent, holdover_rate_percent |
| Extra cost per month if missed | $4,508.83 | current_rent, holdover_rate_percent |
| Notice method / clause | certified mail or nationally recognized overnight courier / Section 11 | notice_method, notice_section |

Money is `Decimal`, rounded half up to cents. Lines with missing or blocked inputs are skipped with a reason (tested for each input).

### The planner

- The model writes **prose with `{tokens}`** (`{deadline}`, `{current_rent}`, `{landlord}`, `{notice_section}`, ...). Code substitutes them. A proposal is rejected if it uses an unknown or unavailable token, or if a date or number the model typed literally does not match the brief (tested: wrong rent, wrong percent, wrong date, an invented "45 days", an extra `$8,500.00` in an email).
- **Reminders**: code computes the 60/30/14-day dates and drops any that are not in the future (tested with today = 2026-12-10: only 12-17 remains). Any other date is rejected. Code appends the clause quote with its page, notice method and section, the holdover consequence, and a link `/#run={id}&page=3`.
- **Emails**: `renew` and `terminate`, the tenant's notice to the landlord. Code requires landlord, tenant, deadline, section and current rent to be present (whichever are available) and the right verb. Recipient stays `{{RECIPIENT}}`.
- **Tracker record** is built entirely by code (the model cannot propose `upsert_record`): deadline, counterparty, premises, current rent, holdover monthly rent and extra, `holdover_risk` text, status `decision needed`.
- Source ids on each action = the model's ids plus every field its inserted text uses (the first reminder cites 6 fields across pages 2 and 3, which is what makes the link fix matter).
- **Mutual exclusion**: approving one draft sets the other to `rejected` (policy reason says why), only within the same run; the second then gets 409. Rejecting one leaves the other open. Reminders are a `bundle`: independent approvals, plus an "Approve all" button.

### Example output (final lease run, reminder 1 of 3)

> Prepare to send written notice to renew or terminate the lease by December 31, 2026.
> 60 days before the notice deadline of December 31, 2026.
> Clause (page 3): "at least ninety (90) days before the Expiration Date"
> Notice must be sent by: certified mail or nationally recognized overnight courier (Section 11).
> If the deadline is missed: holdover rent of $13,526.48/month (150% of the current rent of $9,017.65/month), which is $4,508.83/month more.
> Source: /#run={{RUN_ID}}&page=3

(`{{RUN_ID}}` is replaced when the action is saved; the eval does not save, so it shows the placeholder.)

## 3. Eval results (final code, one run each, 2026-10-22)

| Fixture | Fields | Obligation | Brief (6 numbers + cites) | Action checks | rent_amount | Calls | Tokens | Cost | Latency |
|---|---|---|---|---|---|---|---|---|---|
| `lease.pdf` | 14/14 | 2026-12-31 | yes | 7/7 | 8500 | 2 | 7843 | $0.00162 | 21.8 s |
| `lease_injected.pdf` | 14/14 | 2026-12-31 | yes | 7/7 | 8500 | 2 | 7838 | $0.00161 | 21.4 s |
| `lease_injected_2.pdf` | 14/14 | 2026-12-31 | yes | 7/7 | 8500 | 2 | 7844 | $0.00162 | 18.8 s |

Result files: `evals/results/lease_20261022_075437.json`, `lease_injected_20261022_075500.json`, `lease_injected_2_20261022_075521.json`.

### Eval history (honest record, all kept)

| File | What happened | Fix |
|---|---|---|
| `lease_..._075118` | 14/14 fields; actions 5/7. `notice_section` got `quote_rule_failed` (my quote rule was case-sensitive, "Section 11" did not match), so it was blocked, the model typed `{notice_section}` and was rejected, and both emails lacked the section | Case-insensitive rule |
| `lease_..._075214` | Passed 7/7, but reading the output: the `notice_section` quote was "Section 11", which is **not in the lease** (heading is "11. Renewal..."). It grounded at 0.94, most likely onto "Section 16". The UI would have highlighted the wrong clause | Quotes of 3 words or fewer must match exactly; prompt asks for the heading as printed |
| `lease_..._075316` | Model returned `current_rent` = 9017.0 for the quote "$9,017.65". Validators flagged `value_not_in_quote` + `conflicting_values` and blocked it; no holdover numbers, no emails (action checks 1/7). The safety net worked, the plan was worse | Prompt: amounts keep their cents |
| `lease_..._075355` | Model returned the whole heading as the `notice_section` value; emails lacked "Section 11" | Code normalizes the value to "Section 11" |

One call-free run (a typo of mine in `extract.py`, no API call was made) was deleted since it contained no data.

## 4. Tests (159 passing, `.\.venv\Scripts\python.exe -m pytest -q`, about 5 s, no network, no Supabase)

| File | Tests | Covers |
|---|---|---|
| `test_impact.py` | 11 | all brief math incl. half-up rounding, overdue, missing current rent / holdover rate / escalation (each skips only the lines that need it), blocked fields never used (5 flags), no deadline, empty input, citations |
| `test_planner.py` | 28 | full plan, facts block, names filled, sources include every cited field, tracker built by code, model cannot propose it, **number-mismatch rejection** (4 cases + an email), literal-but-matching number accepted, unknown token, email must cite and state its choice, staged dates only, past stages dropped, duplicates, nudge for missing proposals, plus the earlier guarantees (source ids, recipient/URL never from model, blocked fields hidden, limit, read-only tools) |
| `test_decision_api.py` | 7 | brief endpoint (ids, pages, boxes, skips), group fields, **renew/terminate mutual exclusion** (approve rejects other, 409, reject leaves other open, other runs untouched), reminders independent |
| `test_upload_flow.py` | 3 | `POST /documents` on the real `lease.pdf` with a fake LLM: persisted groups, `{{RUN_ID}}` resolved, 6 sources with pages and boxes, brief, run `page_count`, step order, exclusion through the saved run |
| `test_new_fields.py` | 27 | quote rules, value-in-quote, rent-table parser and today's-row cross-check, short quotes exact, section normalization, invalid percent |
| `test_ui_links.py` + `ui/links.test.js` | 1 | multi-source link navigation, anchors built without HTML |
| others | 82 | unchanged parts, with fixtures updated for the new fields |

## 5. Decisions I made on your behalf

1. **Added `rent_escalation_percent`** (not in your field list). The brief's "next-term rent at the escalation rate" needs it, and the expected JSON already had `rent_escalation_percent: 3`.
2. **"Next-term rent" is labelled illustrative.** In this lease the renewal rent is 95% of fair market rent, which no field can give. The line is "rent after one more 3% step" with a note saying so.
3. **A wrong `current_rent` is blocked, not corrected.** I could have replaced it with the table value, but that would put code-chosen values behind an LLM field's provenance. You lose the numbers on a bad run (it happened once), you never act on a wrong one. Say if you want auto-correction from the table.
4. **Today's date goes into the extraction prompt** so the model can pick the row; the code cross-check against the parsed rent table is the real guard.
5. **The brief is recomputed on read, not stored.** Days remaining stays correct when you reopen a run. Action text was fixed at planning time, so it never says "N days left".
6. **Number check scope**: dates the model types must equal a known date; every other number must match a brief number, a lead time (60/30/14) or a token's text. Spelled-out numbers ("ninety") are not checked.
7. **The reminder facts block and the tracker record are built by code**, the model writes only the title and an intro sentence. This was cheaper and safer than asking the model to restate clause, method and consequence.
8. **Migration `0004`** (two nullable text columns) for grouping, applied to Supabase. Additive only.
9. **Tool-call limit 8 to 12, at most 4 model rounds.** The plan went from 2 to 5 model-proposed actions, so 8 left no room for retries. Architecture doc updated.
10. **Short-quote rule applies to every field**: a quote of 3 words or fewer must match exactly. A 3-word company name with OCR noise would now be `ungrounded`. On text-layer PDFs this does not matter; revisit when OCR arrives.
11. **`draft_email` has a new required `variant` argument** (`renew`/`terminate`), popped by the planner and not stored in the action args. Group membership is stored in the new columns.
12. **Source links are `/#run=N&page=M`**, written into reminder text and turned into anchors by the UI (built with DOM nodes, not HTML). They only mean something while the app is running.
13. **`eval_lease.py` computes today-dependent expectations itself** (current rent from the expected rent schedule, which reminders are still in the future). `lease.expected.json` lost its old `actions` list and gained structured expectations. After 2026-12-31 the lease fixture has no future reminders, so those checks become vacuous; the fixture dates would need moving.
14. **`unusable_reason`** (was private `_usable` in `obligations.py`) is shared by obligations and the brief, so they cannot disagree about what is usable.

## 6. Needs your attention

- **UI layout is not browser-tested.** The node test covers link behaviour. The brief card, groups, "Approve all", hash links and CSS were checked by reading and by running every endpoint, not by looking at them. Please try: upload, click each "because of" link, click a brief number, approve one notice and watch the other reject, "Approve all" on the reminders, open `/#run=<id>&page=3`.
- **Upload is slow: 19 to 22 s** in the final runs (one earlier run took 33 s). In the one run where I printed step timings, extraction took 17 s and the planner 16 s (the planner takes extra rounds when it has to retry). Still synchronous; the queue/SSE work on the roadmap is now more important.
- **Runs made before this part** have none of the new fields, so their brief shows only deadline lines and says why. Re-uploading creates a new run.
- **Extraction varies between runs** even at temperature 0 (see eval history). The validators catch the failures I saw, but a run can still end with fewer actions.
- **Holdover assumes today's rent is still in effect at expiration.** That is true for this lease (year 3 runs to the expiry); a lease with another escalation before expiry would be off. The line carries that note.
- **Instruction detector, rent-table dependence and the injection limits from report 2 still apply.** The new `current_rent` cross-check also needs a rent table.
- **Supabase**: migration 0004 applied (two nullable columns). I created no data this part: no uploads, only read-only GETs against your existing runs (run 6 returns a brief with just the deadline lines).
- Nothing is merged and nothing was pushed to `main`.

## 7. Commands (PowerShell, repo root)

```powershell
git checkout part-4
.\.venv\Scripts\python.exe -m pytest -q                        # 159 tests, no network
node tests\ui\links.test.js                                    # the link test alone

# evals: about 2 OpenAI calls each
.\.venv\Scripts\python.exe scripts\eval_lease.py
.\.venv\Scripts\python.exe scripts\eval_lease.py --pdf fixtures\demo\lease_injected.pdf
.\.venv\Scripts\python.exe scripts\eval_lease.py --pdf fixtures\demo\lease_injected_2.pdf

.\.venv\Scripts\alembic.exe upgrade head                       # 0004 already applied to Supabase
.\.venv\Scripts\python.exe -m uvicorn paperbound.api.main:app --reload   # then upload fixtures\demo\lease.pdf
```
