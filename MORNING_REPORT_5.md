# Report 5: Part 6 (demo mode, deploy modes, Docker)

Branch: `part-6` (from `main`; pushed, not merged; `main` untouched). Nothing was deployed.

## Headline

- Everything in the brief is built. **272 pytest tests pass** (was 217), `alembic check` is clean.
- **Run Demo works end to end in the test harness**: both recorded runs replay over SSE with narration, fields reveal one at a time, the brief and grouped actions appear, and approvals run in the page with no server calls.
- **Demo mode touches neither the database nor OpenAI**, and the app **starts with no `OPENAI_API_KEY` and no `DATABASE_URL`**. Both are tested in clean subprocesses.
- OpenAI usage: **4 of 10 calls** (2 per recording), gpt-4o-mini, about $0.003. Migration `0005` is applied to Supabase (one new table, one nullable column); I created no rows.
- **One real gap you should read before turning live uploads on** (section "Needs your attention", first bullet): only uploads are gated by the access code, not reading results.

## What was built

| # | Item | Where |
|---|---|---|
| 1 | Recorder | `scripts/record_demo.py` (live pipeline, no DB, as of `AS_OF` default 2026-10-22) writes `fixtures/demo/lease/` and `fixtures/demo/insurance_certificate/`: `source.pdf`, `pages/<n>.png`, `snapshot.json`, `narration.json` |
| 2 | Replay | `paperbound/demo/replay.py` (engine), `paperbound/demo/routes.py`: `GET /demo/fixtures`, `/demo/{fixture}/events` (SSE), `/demo/{fixture}/pages/{n}`, plus `/pages/{n}/image.png` |
| 3 | UI | `web/index.html`: Run Demo dropdown and button, badge, narrated live timeline, progressive field reveal with animated boxes, brief card, grouped actions, browser-side sandbox, "Document type" card in demo too |
| 4 | Deploy modes | `paperbound/deploy.py` (settings), `paperbound/usage.py` (rate limit, budget, expiry), gate in `routes.py`, `GET /config`, `GET /healthz`, lifespan cleanup task in `main.py`, migration `0005` |
| 5 | Docker | `Dockerfile`, `.dockerignore`, `fly.toml`, `requirements-prod.txt` |
| 6 | Tests | `test_demo_replay.py` (27), `test_deploy_modes.py` (27), `test_ui_links.py` (now runs 2 node tests), `tests/ui/demo.test.js`, `tests/ui/dom_stub.js`, `tests/conftest.py` |
| 7 | Docs | `README.md` (Run Demo, Deploy modes, screenshot placeholders), `docs/architecture.md` (section 2 implementation status, deploy modes, API, UI, data model, layout), `.env.example`, this report |

### The two recordings (real runs, as of 2026-10-22)

| Fixture | Fields | Steps replayed | Actions | OpenAI calls | Cost | Snapshot size |
|---|---|---|---|---|---|---|
| `lease` | 14 | parse, classify, extract, validate, obligations, impact, planner (+ gate) | 3 reminders, 2 notice drafts (one exclusive choice), 1 tracker | 2 | $0.00163 | 180 KB + 4 page PNGs |
| `insurance_certificate` | 17 | same | 3 reminders, 1 request email, 1 tracker | 2 | $0.00130 | 57 KB + 1 PNG |

**The lease recording contains a real validator catch**: for `rent_period` the model quoted the escalation sentence ("increase by three percent"), which never says "month". Code flagged it `value_not_in_quote` (confidence 0.70) and kept it out of the planner. I kept it, because snapshots are meant to be real runs, and the hand-edited validate narration explains it. The extra effect is that a viewer sees the "code checks the model" step do something.

### Narration

`record_demo.py` drafts it from each step's recorded data (counts, scores, obligations, brief lines, token and cost figures), and says at every step whether it was code or the model: parse "No model is involved", classify "no model was called" (or the LLM fallback when used), extract "the first place a model is used... nothing it says is trusted yet", validate "Now code checks the model", obligations and impact "plain date arithmetic, with no model", planner "the LLM only writes the wording: code supplies every number", gate "deterministic code, not the model". I hand-edited three spots (the lease rent_period catch, the planner sentence in both files, the umbrella-policy wording). Re-recording keeps an existing `narration.json` unless `--redraft`.

### Replay engine

Events: `meta` (badge, intro), `step` (a `running` event, then the finished one with narration), `field` (phase `extracted` as the extract step streams, then `validated` with confidence and boxes as validate streams), `obligations`, `brief`, `action`, a synthesized `gate` step, `done`. Each step replays in its recorded duration capped at 1.5 s (extract is 9 s to 12 s in the recordings, so it plays in 1.5 s). `?speed=0` sends everything at once (used by tests); `speed` is clamped to 0 to 10.

### Deploy modes

| | `LIVE_UPLOADS_ENABLED` not `true` (default) | `true` |
|---|---|---|
| `POST /documents` | 403, before the database is touched | needs `X-Access-Code` (401 wrong or missing; 503 if the server has no `ACCESS_CODE`, so it fails closed) |
| UI | upload hidden, note "Live uploads are off on this deployment" | upload shown, access-code box shown |
| Limits | none needed | 5 uploads per IP per hour (429), 10 MB (413), 20 pages (413, checked after parsing, before saving or any model call), daily LLM budget 1.00 (429), uploads deleted after 24 h |
| Needs | nothing | `ACCESS_CODE`, `DATABASE_URL`, `OPENAI_API_KEY`, a volume for files |

Limits are environment variables with the defaults you specified (`MAX_UPLOAD_MB`, `MAX_PAGES`, `UPLOADS_PER_IP_PER_HOUR`, `DAILY_LLM_BUDGET_USD`, `UPLOAD_TTL_HOURS`).

## Tests (272 passing, about 17 s, no network, no Supabase, no OpenAI)

| File | Tests | Covers |
|---|---|---|
| `test_demo_replay.py` | 27 | both snapshots are complete and real (files, images, steps, boxes, groups, no `{{RUN_ID}}`), **the recorded brief equals what the code computes from the recorded fields**, narration covers every step and the gate, step timing (cap 1.5 s, floor, extract totals 1.5 s, sleeps equal the plan, speed 0 sleeps nothing), event order and progressive reveal, SSE endpoint, pages and images, 404s and path-traversal names, **replay modules import no database, no openai, no pipeline (clean subprocess)**, demo endpoints work when DB and OpenAI would raise, **app starts and serves the demo with no secrets and no database** (clean subprocess: `/`, `/healthz`, `/config`, demo, upload 403, engine never created) |
| `test_deploy_modes.py` | 27 | disabled by default (5 values, case-insensitive true), 403 with no database, 503 for missing DB, `/config` never leaks the code, access code (missing, empty, wrong, trailing space, right, unconfigured), wrong code does not consume the rate limit, 10 MB and 20 page limits (nothing stored on rejection), env overrides, **rate limit** (6th refused, other IP fine, hashes only, hour window both sides, rejected files count, configurable, IP source), **budget** (blocks at the limit, resets next UTC day, spend recorded per run, blocked upload does no work), expiry (24 h, cleanup removes only expired with files and cascaded children, keeps the ledger, survives missing files, runs on upload, re-upload extends only expiring documents), storage prefix delete stays inside its directory |
| `test_ui_links.py` + `ui/*.js` | 2 | the earlier link test, now also covering `/#demo=` anchors; **the demo page fed the real recorded events**: upload hidden when live is off, badge, narration on every step, running pulse, fields one at a time then validated in place, brief with source links, two groups, "Approve all", exclusive rejection, **no fetch calls during approvals**, stream closed on `done`, re-running resets |
| others | 216 | earlier parts; upload and routing fixtures now enable live mode and send the code; `conftest.py` makes tests ignore `.env` |

## Decisions I made on your behalf

1. **Fixture directories** are `fixtures/demo/lease/` and `fixtures/demo/insurance_certificate/` next to the existing `lease.pdf`, `*.expected.json` files (the evals still glob only the root).
2. **A 0.3 s floor on step replay.** You asked for original timing capped at 1.5 s; four of the seven steps (classify, validate, obligations, impact) took under 50 ms, which would flash their narration past unreadably. The cap is exactly as asked; the floor is mine.
3. **A `running` event before each step and a synthesized `gate` step** (from `narration.steps.gate`, matching the architecture's "gate" example). Neither is in the snapshot's `steps`.
4. **Field reveal in two phases**: value and quote during extract (chip "checking"), then confidence, flags and an animated box during validate. This is true to the pipeline: the model's answer arrives first and code checks it after.
5. **Recorded links** in reminders point at `/#demo=<fixture>&page=N`, and the page scrolls to that page of the open demo.
6. **The engine is lazy**: `db/session.py` now creates its engine on first use; a request that needs the database answers 503 when `DATABASE_URL` is empty. `PAPERBOUND_SKIP_DOTENV=1` stops `config.py` reading `.env` (used by tests, the container, and the no-secrets tests).
7. **Tests no longer read your `.env`** (`tests/conftest.py`): they strip deployment variables and skip dotenv, so a test can never reach your database or key.
8. **403 comes from a route dependency that runs first**, so a demo-only deployment answers without opening a database session.
9. **Access code**: header `X-Access-Code`, constant-time compare; an enabled server with no `ACCESS_CODE` refuses every upload (503) rather than allowing all.
10. **Rate limit order**: code check, then cleanup, then rate limit, then budget, then size and PDF checks. A request that passed the code counts toward the limit even if the file is then rejected (so garbage uploads cannot be free probes); a wrong code does not count. Keyed on `Fly-Client-IP`, else the socket address; only a SHA-256 of it is stored.
11. **Budget is checked before a run and recorded after**, so one upload can overshoot by one run's cost (a few tenths of a cent today). Day boundary is midnight UTC.
12. **Expiry uses a new nullable `documents.expires_at`**, set only for uploads made in live mode. I did this instead of "delete documents older than 24 h" so that turning live mode on locally can never delete your existing documents 2 and 4. Cleanup runs on each upload and every 15 minutes in the background.
13. **Migration `0005` applied to Supabase**: new table `usage_ledger` (rate limit and budget) and nullable column `documents.expires_at`. Additive only.
14. **`requirements-prod.txt`** (new): only what the app imports plus uvicorn, so the image does not carry pytest, reportlab, onnxruntime and friends from the full freeze. I verified it by installing it in a fresh virtualenv and running the demo app, a real PDF parse and the 403 path with it (Windows only, see below).
15. **The container runs as root**: a Fly volume mounts root-owned, and a non-root user could not write uploads to it. `PAPERBOUND_SKIP_DOTENV=1` is baked in so a stray `.env` can never be read.
16. **`fly.toml` is demo-only by default** (no mounts, `LIVE_UPLOADS_ENABLED = "false"`, no release command, because a demo-only deploy has no database to migrate). Machines stop when idle.
17. **UI refactor**: field rows, brief rendering, and `decide()` are mode-aware; the page initializes on `load` by asking `/config` and `/demo/fixtures`.

## Needs your attention

- **Reading results is not gated by the access code, only uploading is.** In live mode, `GET /runs/{id}/...`, `/documents/{id}/pages/{n}` and `/files/...` answer anyone, and ids are sequential, so someone could read other people's uploads during their 24 hours. The default (demo only) is not affected, but **do not turn live uploads on for a public site until this is closed**. The fix I would make is a signed cookie set when a valid access code is used (images cannot send a header), checked by every non-demo endpoint. I did not build it because it changes every endpoint and the page's fetches, which is more than this brief asked for.
- **Docker was not built.** Docker Desktop's engine is not running here, so I could not build or run the image. What I verified instead: `requirements-prod.txt` installs and runs the app in a clean venv (Windows), `fly.toml` parses, and the container's start command is the one used in tests. Linux-specific problems (a missing shared library for PyMuPDF; I added `libgomp1` and `libglib2.0-0` as insurance) would only show at the first `fly deploy`.
- **flyctl was not run either.** Check the flags below with `fly launch --help` and `fly deploy --help` if your version differs.
- **The page was tested on a DOM stub, not in a browser.** Layout, the pulse and flash animations and the dropdown were not seen. The logic (events, progressive reveal, grouping, sandbox, no server calls) is tested with the real recorded events. Please click through Run Demo on both fixtures.
- **Live mode is single-machine**: uploaded files live on one volume, so keep one machine (`fly scale count 1`). The limits themselves are in Postgres and would hold across machines.
- **The IP rate limit trusts `Fly-Client-IP`**, which is right behind Fly's proxy and spoofable anywhere else.
- **Demo snapshots go stale if the pipeline changes.** A test checks the recorded brief against the code, so a change to brief logic will fail it until you re-record (about 4 calls, `scripts/record_demo.py`).
- **The recorded lease shows a flagged field** (`rent_period`), described above. If you would rather the demo opens on a flag-free run, re-record (the model varies run to run) and `--redraft` the narration.
- Nothing merged, nothing pushed to `main`.

## Deploy commands (not run)

Demo only, no secrets, no database. Run from the repo root. Change the app name (Fly app names are global) and region as you like.

```powershell
fly auth login
fly launch --no-deploy --copy-config --name <your-app-name> --region ewr
#   keep the existing fly.toml; answer "no" to any Postgres, Redis or Tigris prompt
fly deploy
fly open
```

Live uploads (only after the read-gating gap above is closed, or for a private audience). Your `DATABASE_URL` must be the Supabase **session pooler** URL (port 5432); `0005` is already applied there.

```powershell
fly volumes create paperbound_data --size 1 --region ewr
# edit fly.toml: set LIVE_UPLOADS_ENABLED = "true" and uncomment the [mounts] block
fly secrets set ACCESS_CODE="<a long random string>" DATABASE_URL="<supabase session pooler url>" OPENAI_API_KEY="<sk-...>" DAILY_LLM_BUDGET_USD="1.00"
fly deploy
fly scale count 1
fly logs
```

Tuning later (each triggers a restart): `fly secrets set MAX_UPLOAD_MB=10 MAX_PAGES=20 UPLOADS_PER_IP_PER_HOUR=5 UPLOAD_TTL_HOURS=24`. Back to demo only: `fly secrets unset ACCESS_CODE DATABASE_URL OPENAI_API_KEY` and set `LIVE_UPLOADS_ENABLED = "false"`, then `fly deploy`.

## Commands (PowerShell, repo root)

```powershell
git checkout part-6
.\.venv\Scripts\python.exe -m pytest -q                         # 272 tests, no network, no database
node tests\ui\links.test.js                                     # the link test alone

# the demo, with nothing configured
$env:PAPERBOUND_SKIP_DOTENV = "1"
.\.venv\Scripts\python.exe -m uvicorn paperbound.api.main:app --reload
#   open http://127.0.0.1:8000/ and press Run Demo (upload is hidden)

# re-record both demos (about 4 OpenAI calls; keeps narration.json unless --redraft)
.\.venv\Scripts\python.exe scripts\record_demo.py
```
