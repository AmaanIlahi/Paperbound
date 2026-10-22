# Report 4: Part 5 (insurance certificates, classifier, clock injection)

Branch: `part-5` (from `main`; pushed, not merged; `main` untouched).

## Headline

- Everything in the brief is built. **217 pytest tests pass** (was 159), `alembic check` is clean, **no new migration** (`documents.doc_type` already existed).
- Final evals, as of 2026-10-22, on all four fixtures: every fixture is classified by **heuristics alone** and passes every check. Leases: 14/14 fields, 6/6 brief numbers, 7/7 action checks (all three, injections included, `rent_amount` 8500). Certificate: **18/18 fields, 5/5 brief lines, 5/5 action checks**.
- OpenAI usage: **13 of 30 calls**, gpt-4o-mini only, about $0.009 in total.
- Supabase: I created no data this part (no uploads), and ran no migration.

## What was built

| # | Item | Where |
|---|---|---|
| 1 | Clock | `paperbound/clock.py`: `today(override=None)`: parameter, then `AS_OF`, then the system date; a malformed `AS_OF` is a clear error. Used by the extraction prompt, rent-table row selection, `impact.py`, the planner's reminder dates and the runner. The last `date.today()` calls are gone from `paperbound/` (grep-verified; only `clock.py` has one) |
| 2 | Certificate fixture | `scripts/make_sample_coi.py` writes `fixtures/demo/insurance_certificate.pdf` (one page, ACORD-style: producer Northgate Risk Partners LLC, insurers Meridian Mutual and Pinecrest Casualty, insured Brightline Studio Inc., holder Harborview Properties LLC, GL-5521904 01/15/2026 to 01/15/2027 with $1M / $2M, UM-7730418 03/01/2026 to 03/01/2027 with $5M / $5M) and `insurance_certificate.expected.json` |
| 3 | Classifier | `pipeline/classify.py`, a `classify` pipeline step (decision, reason, and model/tokens/cost when the LLM was used) |
| 4 | Schema + validation | `schemas/insurance.py` (`InsuranceCertFields`, `PolicyFields`); `validate.py` refactored so lease and certificate share one per-field validator (`validate_coi`) |
| 5 | Certificate brief | `impact.py` `build_coi_brief`; `obligations.py` `derive_coi_obligations`; `config.coi_min_each_occurrence()` |
| 6 | Certificate planner | `agent/profiles.py` (lease and certificate profiles); `planner.py` is now type-agnostic |
| 7 | Routing | `doctypes.py` registry; `runner.py` classifies first; `routes.py` sets `documents.doc_type`, returns `doc_type` and `message`; UI "Document type" card |
| 8 | Eval | `scripts/eval_lease.py` renamed (`git mv`) to `eval_fixtures.py`; runs every `fixtures/demo/*.expected.json` |
| 9 | Tests | 4 new files plus fixtures (below) |
| 10 | Docs | `README.md` (results, how it works, commands), `docs/architecture.md` (4.2 rewritten, 4.3, 4.4, 4.5, 4.5b, 4.6, API, UI, layout, evals) |

### Classifier (how it decides)

Weighted key phrases per type, each counted once (certificate: "certificate of liability insurance" 4, "certificate holder" 3, "insurers affording coverage" 3, "policy number" 2, "each occurrence" 2 ...; lease: "lease agreement" 4, "commercial lease" 4, "landlord" 2, "base rent" 2 ...). Layout signals: a key phrase in the first four lines of page 1 adds 4 (the title), at most 2 pages adds 1 for a certificate, 3 or more pages adds 1 for a lease (only to a type that already has hits). The top type wins outright at a score of 6 or more with a lead of 4; otherwise gpt-4o-mini decides from the first two pages, as an enum plus a one-sentence reason, with the text marked untrusted.

On the fixtures: the real lease scores 24 against 1 for a certificate, the certificate 28 against 2. In a one-off check, an invoice scored 0 on both and the LLM fallback answered `other` ("The document is an invoice, which does not fit...", 227 tokens, $0.00005).

### Routing

`POST /documents` runs `classify`, then the schema, validation, obligations, brief and planner that match the type. `other` stops after the classify step with run status `done` and the message "document type not supported yet" (response `message`, `GET /runs/{id}` `message`, and a notice in the UI). Only `parse` and `classify` steps are recorded for it. A classifier failure gives status `partial` and later stages `skipped`. The pages of an unsupported document stay viewable.

### Certificate decision brief (as of 2026-10-22)

| Line | Value | Cites |
|---|---|---|
| Earliest policy expiration | January 15, 2027 | both policies' expiration fields |
| Days until it expires | 85 | the same |
| Policies expiring within 60 days | None | the same |
| General Liability each occurrence | $1,000,000, meets the $1,000,000 minimum | its limit and type |
| Umbrella each occurrence | $5,000,000, meets the $1,000,000 minimum | its limit and type |

### Certificate actions (final run)

3 reminders (2026-11-16, 2026-12-16, 2027-01-01), each listing every policy with its expiry and limit check, the quote and page of the earliest expiration, and a `/#run=N&page=1` link; one draft email from Harborview Properties LLC to Brightline Studio Inc. asking for a renewed certificate before January 15, 2027; one tracker record (`renewal needed`, per-policy expiry and `meets_minimum`). All pending, recipient `{{RECIPIENT}}`.

## Eval results (final code, one run each, as of 2026-10-22)

| Fixture | Classified | Fields | Obligations | Brief | Action checks | Calls | Tokens | Cost | Latency |
|---|---|---|---|---|---|---|---|---|---|
| `insurance_certificate.pdf` | insurance_certificate (heuristic) | 18/18 | correct | 5/5 | 5/5 | 2 | 6117 | $0.00131 | 16.6 s |
| `lease.pdf` | lease (heuristic) | 14/14 | correct | 6/6 | 7/7 | 2 | 7912 | $0.00164 | 29.5 s |
| `lease_injected.pdf` | lease (heuristic) | 14/14 | correct | 6/6 | 7/7 | 2 | 7848 | $0.00162 | 17.2 s |
| `lease_injected_2.pdf` | lease (heuristic) | 14/14 | correct | 6/6 | 7/7 | 2 | 7855 | $0.00162 | 18.2 s |

Result files: `evals/results/insurance_certificate_20261022_094944.json`, `lease_20261022_095013.json`, `lease_injected_20261022_095030.json`, `lease_injected_2_20261022_095049.json`.

### Eval history (honest record)

- `insurance_certificate_20261022_094902.json` (first ever certificate run): 18/18 fields and 5/5 checks, but the planner needed 3 rounds (4 calls): twice the email was rejected with "must cite {certificate_holder}" because the model wrote from the holder's point of view without naming it. The code caught it and the third try passed. I tightened the prompt (the text must contain `{insured}`, `{certificate_holder}` and `{earliest_expiration}`); the final run took 2 calls. Nothing else failed on any certificate run.

## Tests (217 passing, `.\.venv\Scripts\python.exe -m pytest -q`, about 8 s, no network)

| File | Tests | Covers |
|---|---|---|
| `test_clock.py` | 11 | parameter beats env beats system date, blank env, 3 bad `AS_OF` values, and that the extraction prompt, rent-table row, brief days, reminder dates and the whole pipeline each follow the clock |
| `test_classify.py` | 11 | real lease, both injected leases and the real certificate decided by heuristics with an LLM that fails the test if called; a lease that mentions insurance certificates; title and page-count signals; LLM fallback (unrelated text, mixed signals), what the LLM sees, enum-only answers, failure |
| `test_coi.py` | 30 | validation (clean fixture, short-quote exactness, quote rules, wrong amounts, date order, instruction in a quote, missing values), obligations, **brief math** (earliest, days, 60-day window edges 60/61, expired, configurable minimum via param and env, equal-meets, blocked limit, blocked expiration, nothing usable), **planner** (full plan, email content and placeholders, facts block, below-minimum wording, tracker record, number-mismatch rejection, literal match accepted, email requirements, lease variants/tokens rejected, future-only stages, blocked policy hidden) |
| `test_routing.py` | 6 | `other` stops after classify (steps, message, no fields/actions, status done, doc type stored, extractor and planner never called), unsupported pages still viewable, classifier failure, certificate end to end through the saved run (steps, fields, obligations, actions, links, brief with sources), lease path, runs without a stored type |
| others | 159 | earlier parts; runner and upload-flow fixtures gained a classifier |

## Decisions I made on your behalf

1. **Clock priority**: explicit parameter, then `AS_OF`, then the system date. In `run_pipeline` the extractor now receives the pipeline's `today` through the doc-type registry, so the extraction prompt cannot disagree with the rest of the run. `eval_fixtures.py` picks the date from `--as-of`, then the expected file's `as_of`, then `AS_OF`, then 2026-10-22, and sets `AS_OF` for the process.
2. **Policy fields are named `policy_1.number`, `policy_2.expiration_date`, ...** in the existing `fields` table (no new table). Validation rules are keyed by the part after the dot. Policies per certificate are capped at 6 in the schema description (not enforced by the schema itself).
3. **One `expiration` obligation per usable policy**, not one for the earliest. The planner keys reminders off the earliest.
4. **`expiring_soon` counts expired policies** too (60 days or fewer, including negative), and 60 days exactly is inside the window. A limit equal to the minimum meets it.
5. **A blocked policy is left out of "earliest expiration", with a note saying so**, which means the earliest date can be later than the real one if the blocked policy expires first. The note is on the brief line and in the UI; reminders are then based on the remaining policies.
6. **Zero keyword hits goes to the LLM.** "Unsure" includes "nothing recognised", so every unsupported document costs one call (about $0.00005). Heuristics never answer `other` on their own.
7. **Run steps for `other`**: only `parse` and `classify` are written (no `skipped` rows), because stopping is intended, not a failure. A classifier failure is a failure and gets `skipped` rows.
8. **`classify` is now a step of every run**, so step order for leases changed (parse, classify, extract, ...).
9. **Planner profiles**: lease behaviour moved verbatim into `LeaseProfile` (the 28 lease planner tests pass unchanged except imports); `CoiProfile` adds the certificate rules. `draft_email`'s `variant` enum gained `request_certificate`. The certificate email is a single draft, so it has no exclusive group. The certificate profile also exposes a `{deadline}` token as an alias of `{earliest_expiration}`.
10. **Insurance limits print without cents** (`$1,000,000`); rent keeps cents.
11. **Certificate tracker key** is `coverage-expiry:<earliest date>:<insured slug>`; the record also carries `below_minimum` (names) only when a policy fails the minimum, and `expiring_within_60_days`.
12. **Eval file layout changed**: each `*.expected.json` now names `doc_type`, `as_of` and `pdfs`; `lease.expected.json` gained those three keys. `eval_lease.py` is renamed with `git mv` (history kept) and loses `--pdf` and `--expected` in favour of `--only` and `--as-of`. Policies are matched to expected ones by policy number, so the order the model lists them in does not matter.
13. **Brief endpoint by type**: a run whose document has no stored type (all runs before this part) is treated as a lease.
14. **Prompt change after the first live certificate run** (see eval history). It was the only prompt change.

## Needs your attention

- **UI not browser-tested again.** The new "Document type" card and the certificate rendering were checked through the API tests and by reading the code, not by looking at them. The earlier node link test still passes. Please upload both PDFs and the invoice-like case (any non-lease, non-certificate PDF).
- **The classifier was tuned on my own fixtures** (and checked on one invoice). There are no held-out real documents, so the thresholds (6 and 4) and weights are plausible, not validated. A real ACORD form will have different wording and layout from my fixture; extraction on a real certificate is untested. A genuinely mixed document goes to the LLM, which is the safe direction.
- **Classification can be gamed** by keyword stuffing (a document full of certificate phrases is classified as one). It then still has to pass extraction and every validator, so the impact is limited to wasted calls and a wrong schema, but it is not a security boundary.
- **Upload latency** is 17 to 30 s (one lease run took 29.5 s, the certificate 16.6 s). Extraction dominates; synchronous.
- **Certificate numbers lean on short quotes being exact** (policy number, a date). The prompt asks for labelled quotes, and the live model got all 18 right, but a model that quotes a number with a typo is marked ungrounded and its policy loses that line, by design.
- **The dated fixtures age.** Pinning `as_of` keeps the evals stable; run without a pinned date, the certificate has no future reminders after 2027-01-01 and the lease after 2026-12-17.
- Nothing merged, nothing pushed to `main`.

## Commands (PowerShell, repo root)

```powershell
git checkout part-5
.\.venv\Scripts\python.exe -m pytest -q                          # 217 tests, no network, no database

# evals: about 2 OpenAI calls per fixture, none for classification
.\.venv\Scripts\python.exe scripts\eval_fixtures.py              # all four, one table per doc type
.\.venv\Scripts\python.exe scripts\eval_fixtures.py --only insurance
.\.venv\Scripts\python.exe scripts\eval_fixtures.py --as-of 2026-11-20

.\.venv\Scripts\python.exe scripts\make_sample_coi.py            # regenerates the certificate and its expected JSON
$env:AS_OF = "2026-10-22"                                        # replay the app as of that day
.\.venv\Scripts\python.exe -m uvicorn paperbound.api.main:app --reload
```
