# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hiring site: applicants upload a CV for an open role; a pipeline of agents cleans the CV, extracts
job criteria from the ad, ranks candidates, and gives a detailed top-5 writeup with quotes. Two halves:

- **Node/Express site** (ESM, `"type": "module"`) — serves pages, handles auth/applications/roles, and
  shells out to Python for all CV/agent work.
- **Python pipeline** (`cv_reader/`, `hiring/`) — runs as subprocesses invoked via `python -m cv_reader`
  / `python -m hiring <cmd>`, never imported directly into the Node process.

## Commands

```
npm install
pip install -r cv_reader/requirements.txt
cp .env.example .env            # then check LLM_BACKEND etc.
npm run dev                      # node --watch index.js, http://localhost:3000
npm start                        # node index.js (no watch)
npm run create-admin -- <username> <password>
npm run create-admin -- --list
```

No test suite, linter, or build step is configured in this repo.

Run the Python agents directly from the terminal (same entry points the server calls):
```
python -m cv_reader cvs/ -o out                                  # Oscar on a folder
python -m hiring clara --input clara_in.json --out job.json
python -m hiring remi  --job job.json --runs out --out remi.json [--ids C0001,C0002] [--llm-gate]
python -m hiring jules --job job.json --runs out --remi remi.json --out jules.json
```
`cv_reader` exit codes: per-file status is `ok`/`warning`/`failed` (never aborts a batch). `hiring` exit
codes: 0 ok, 2 bad input, 3 the LLM could not be used.

## Architecture

### Request flow (Node)

`index.js` wires everything: loads `.env` via `env.js` (first import, no extra dependency), sets up
cookie-session auth, mounts `roleRoutes` (`roles.js`) and `applyRoutes` (`apply.js`), and serves
`index.html` / `styles.css` publicly while everything under `/pages` requires `requireAuth`.

- `auth.js` — users + sessions as JSON files in `data/` (git-ignored), scrypt password hashing, httpOnly
  `sid` cookie, atomic writes (`.tmp` + rename). No session store/DB.
- `rolesStore.js` — one role per open position, stored in `data/roles.json`; each role's agent output
  lives under `data/roles/<id>/` (`job.json`, `clara_out.json`, `remi.json`, `jules.json`,
  `progress.json`). Owns the validation/sanitizing of what the admin UI sends (`cleanJob`,
  `cleanRules`, `cleanCriterion`).
- `apply.js` — multipart upload (multer, 5 MB, pdf/docx/txt only) → `runCvReader` → appends to
  `data/applicants.json`. A single `Promise` chain (`queue`/`inQueue`) serializes all CV processing
  (one Python subprocess at a time) so candidate numbering stays safe. Each applicant stores
  `cvSha256`; the same file for the same role is refused with 409 (checked inside the queue).
- Sign-up (`index.js`) is closed by default: only allowed while no admin exists, or with
  `ALLOW_SIGNUP=true`. Other admins are created with `npm run create-admin`.
- `env.js` — tiny `.env` parser (`parseEnv`): `#` after whitespace starts a comment unless the value
  is quoted. Python reads numeric settings through `hiring.llm.env_number`, which tolerates the same.
- `cvRunner.js` — runs `python -m cv_reader` on one file, assigns it `C####`, and reads back
  `batch_summary.json` + the cleaned text from `data/runs/<candidateId>/`.
- `roles.js` — per-role agent endpoints (`clara`/`oscar`/`remi`/`jules`). Each agent runs in the
  background via `startAgent`: only one agent per role at a time, state tracked as
  `running`/`done`/`failed` in `role.agents`, polled from the admin UI via `GET .../results`
  (which also returns `progress.json` written by the Python side for live progress bars). On server
  restart, any role left in `running` state is marked `failed` (see `rolesStore.js` top-level loop).
- `pyRunner.js` — the only place that spawns Python (`execFile`, no shell — untrusted filenames/text
  can never become shell commands). `PYTHON_BIN` env var picks the interpreter.

Data directories (`data/uploads`, `data/runs`, `data/roles`) and JSON files are all git-ignored and
created on demand; there is no database.

### CV pipeline (`cv_reader/`) — "Oscar"

Seven stages, each file owning one (see `cv_reader/pipeline.py` for the full map and
`cv_reader/pii.py` for personal-info extraction):

1. **Load** (`loaders/`) — detect file type by content, open it (pdf/docx/txt, OCR fallback for
   scanned PDFs via Tesseract).
2. **Extract** (`loaders/`) — raw lines + layout hints.
3. **Normalize** (`normalize.py`) — clean up extraction mess.
4. **Segment** (`segment.py`) — label blocks/entries into sections; PII (name, contact info) is split
   out to a separate record here (`pii.py`) so it never reaches the LLM-facing text.
5. **Rules** (`rules/engine.py`) — keep/drop per block and per line, driven by a role's
   `rules.sections` settings (defaults: drop `interests`, `references`, `declaration`).
6. **Assemble** (`assemble.py`) — groups kept blocks back into one output text block per CV.
7. **Record** (`structure.py`, `embed.py`) — structured JSON (jobs+dates, total years, skills,
   languages, availability) and an embedding (the "meaning" fingerprint Remi compares against).

`vocab/synonyms.yaml` standardizes vocabulary (e.g. "ReactJS" / "dev front" → one skill) — safe to
edit freely, no code changes needed. Each CV is processed independently: one bad file is reported in
its own result and never aborts the batch. Outputs per candidate under `data/runs/<id>/`: cleaned
text (kept sections only), full text (all sections except references, PII removed — what Jules
reads), structured record JSON.

### Hiring pipeline (`hiring/`) — Clara, Remi, Jules

Runs against Oscar's output in `--runs` dir. All three subcommands go through `hiring/cli.py`.

- **Clara** (`clara.py`) — job ad text and/or admin's must/nice lists → `must_haves` +
  `nice_to_haves` with keywords (LLM call). Admin reviews/edits before saving via the role's
  `job` field.
- **Remi** (`remi.py`) — must-have gate (PASS eliminates nothing / FAIL eliminates / UNKNOWN only
  penalizes), then ranks the survivors by keyword match + embedding "meaning" similarity +
  nice-to-haves, keeps top 20. Mostly plain code; `--llm-gate` adds an optional Claude double-check
  of the must-have gate.
- **Jules** (`jules.py`) — for the Remi top 20, has Claude read each full CV criterion-by-criterion
  with word-for-word quotes; `citation_checker.py` verifies every quote actually appears in the
  source text before it's trusted; outputs top 5 with detailed scores and what's missing.
- **LLM backend abstraction** (`llm.py`) — single `complete_json(system, prompt, schema)` entry point
  used by Clara/Remi/Jules, selected via `LLM_BACKEND` env var:
  - `claude_cli` (default) — shells out to the local `claude` CLI, uses the operator's Claude
    subscription, no API key (demo-oriented; subject to plan usage limits).
  - `api` — Anthropic API, needs `ANTHROPIC_API_KEY`.
  - `ollama` — local model via `OLLAMA_URL` (default `http://localhost:11434`).
  Switch backends purely via `.env`; agent code never hardcodes a provider.
- `store.py` — reads candidate data (`load_candidate`, `load_personal`, `list_candidates`) from the
  `--runs` directory structure that `cv_reader` produces.
- `criteria.py` — shared cleaning/normalizing of must-have/nice-to-have criteria structures (mirrors
  the Node-side `cleanCriterion` in `rolesStore.js` — keep both in sync if the criterion shape
  changes).

Progress files (`--progress path.json`) are written as `{stage, done, total}` so the Node layer can
poll and show a live progress indicator while an agent runs in the background.

## Key conventions

- **Admin vs. applicant separation**: `index.html` is the only page applicants see (role picker +
  upload form, public `/api/apply`, public `/api/roles`). Everything else — `pages/adminlobby.html`,
  `pages/roles.html`, and all `/api/admin/*` routes — requires a logged-in admin session.
- **Personal info never reaches the LLM-facing text.** `pii.py` strips it out during Oscar's segment
  stage into a separate record; Jules and Remi only ever see the de-identified text/record.
  Referees' contact details are stripped too (into a throwaway store, never the candidate's record),
  and the `references` section is always left out of the full text (`FULL_TEXT_DROP` in
  `pipeline.py`), whatever the role's keep/drop settings.
- **One agent per role at a time** (`startAgent` in `roles.js`): starting Remi while Clara/Oscar/Jules
  is running for the same role returns 409. Agents run as detached background work; the client polls
  `GET /api/admin/roles/:id/results`.
- **Running Remi clears a stale `jules.json`** for that role (a new shortlist invalidates the old
  top-5 writeup) — see `roles.js`'s `/remi` handler.
- JSON "databases" (`data/*.json`) are always written atomically: write to `<file>.tmp`, then
  `fs.renameSync` over the real file. Follow this pattern for any new persisted state.
- All Python invocations go through `pyRunner.js`'s `execFile` (never a shell) so filenames/user text
  can't be interpreted as shell commands — keep any new Python-calling code on that path.
