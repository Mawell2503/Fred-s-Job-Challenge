# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hiring site: applicants upload a CV (+ required practical details, optional LinkedIn PDF) for an open
role; a pipeline of agents cleans the CV, extracts job criteria from the ad, ranks candidates, reads the
top 20 in full to keep a top 10, then a committee (Hélène, Victor, Nina) debates those 10 to pick a top 5
and Gaston writes the report. Two halves:

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
npm run demo                     # demo role + 15 fictional applications (demo/)
npm run pipeline -- <roleId>     # the full pipeline from the terminal (stop the server first)
npm run import -- <folder>       # a folder of CVs into the pool, invented practical details (stop the server first)
npm test                         # node --test tests/*.test.mjs && python3 -m unittest discover -s tests
```

Tests use only the standard libraries (`node:test`, `unittest`) and `LLM_BACKEND=fake`: no AI, no cost.
`tests/helpers.py` builds committee packets in memory. No linter or build step.

Run the Python agents directly from the terminal (same entry points the server calls):
```
python -m cv_reader cvs/ -o out                                  # Oscar on a folder
python -m hiring clara --input clara_in.json --out job.json
python -m hiring remi  --job job.json --runs out --out remi.json [--ids C0001,C0002] [--llm-gate]
python -m hiring jules --job job.json --runs out --remi remi.json --out jules.json   # keeps --top 10
python -m hiring committee --job job.json --runs out --jules jules.json --input committee_in.json \
                           --out committee.json [--report report.md] [--trace trace.jsonl] [--feed feed.json]
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
  `progress.json`, and `runs/` + `reads.json` when it has its own reads). Owns the validation/sanitizing
  of what the admin UI sends (`cleanJob`, `cleanRules`, `cleanCriterion`), `DEFAULT_RULES` (the sections
  every CV is read with on arrival), `rulesKey`, and per role `scope` (`pool` | `direct`) and `tieBreak`.
- `apply.js` — routes only: multipart upload (multer, 5 MB, fields `resume` pdf/docx/txt and optional
  `linkedin` pdf, plus a required `practical` JSON string) → `submitApplication`. Admin reads:
  `GET /api/admin/applicants?role=&details=1` (with practical answers), `.../:id/cv` (the original file,
  `nosniff`, sandboxed unless PDF), `.../:id/{text,full,record,practical,linkedin}`.
- `applicants.js` — no express: the **CV pool** (`data/applicants.json`, written compact), the single
  `Promise` queue (`inQueue`, one Python subprocess at a time so candidate numbering stays safe),
  `submitApplication({ roleId | null, ... })`, `refreshDefaultReads`, `applicantsInScope(role)`,
  `directApplicants`, `applicationFor(a, roleId)` → `this | open | other`. Applicants do NOT belong to a
  role: each stores `appliedFor` (role ids; `[]` = open application), `cvSha256` (one CV once in the whole
  pool; the same CV for another role is added to `appliedFor`), `rulesKey` (the sections its arrival read
  used), `cvName`, `linkedinFile`, `practical`, `source` (`form` | `import` | `demo`). On start, records from
  before the pool are converted once (`roleId` → `appliedFor`, right-to-work → `work_countries`).
- `reads.js` — no express: Oscar's reads per role. A role whose sections equal `DEFAULT_RULES` uses the
  arrival reads in `data/runs/`; any other role gets its own in `data/roles/<id>/runs/`, indexed by
  `reads.json` (`{cid: {key, status, ...}}`) and only redone when its `rulesKey` changes
  (`ensureReads`, `staleCount`, `runsDirFor`).
- `practical.js` — no express: `cleanPractical` (applicant answers, required: salary with the currency
  they choose, `work_countries` instead of right-to-work), `cleanLogistics` (the role's practical
  requirements for Nina), `upgradePractical` (old records), `publicRoleInfo` (what the public form may
  know: currency and work country, never the budget).
- `pipeline.js` — no express: `runPipeline(role, { llmGate }, progressFile)` = who it runs on (fixed at the
  start: `applicantsInScope`) → Oscar (`ensureReads`: only what this role still needs) → Remi (ids through
  `ids.txt`, `--ids-file`: Windows command lines are too short for big pools) → Jules `--top 10` → writes
  `committee_in.json` (practical answers, LinkedIn flags, `names`, and on whole-pool runs `application`
  labels + `tie_break_direct`) → `hiring committee`; sets `role.agents[step]` for each step;
  `startAgent`, `runHiring`, `pipelineProblem`, `applicationLabel` live here too.
- `scripts/import-cvs.js` — `npm run import -- <folder>`: every CV through `submitApplication` (name from
  the CV via `nameFromCv`, else "Test subject #N"), invented practical details seeded by the file's sha256
  (same file → same answers), never touches applicants already in the pool.
- Sign-up (`index.js`) is closed by default: only allowed while no admin exists, or with
  `ALLOW_SIGNUP=true`. Other admins are created with `npm run create-admin`.
- `root.js` — imported first by `index.js`: `chdir`s to the project folder, so the server finds `data/`,
  `.env` and the Python packages wherever it is started from. Only the server imports it (the tests run
  the other modules from a scratch folder on purpose).
- `env.js` — tiny `.env` parser (`parseEnv`): `#` after whitespace starts a comment unless the value
  is quoted. Python reads numeric settings through `hiring.llm.env_number`, which tolerates the same.
- `cvRunner.js` — runs `python -m cv_reader` on one file, assigns it `C####`, and reads back
  `batch_summary.json` + the cleaned text from `data/runs/<candidateId>/`.
- `roles.js` — per-role endpoints: `clara` (manual, the admin must check her criteria) and `pipeline`
  (everything else, one button). Work runs in the background via `startAgent`: one thing per role at a
  time, state in `role.agents` (`pipeline`, `clara`, and per step `oscar`/`remi`/`jules`/`committee`),
  polled from the admin UI via `GET .../results` (also returns `progress.json`, the committee and the live
  `feed.json`). `GET .../report.md` downloads the report. On server restart, anything left `running` is
  marked `failed` on disk (see `rolesStore.js`).
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
  source text before it's trusted; keeps the top 10 with detailed scores and what's missing. An answer
  that skips criteria gets one re-prompt naming them; a 0 on a CV where Remi found must-haves is read
  twice; anything still wrong goes to `warnings` (never a silent low score).
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
- `fake_llm.py` — `LLM_BACKEND=fake`: rule-based answers for every schema `title` (clara, jules,
  remi_gate, committee_position, gaston_report). Tests swap answers with `llm.set_fake_handler`.

### Committee (`hiring/committee/`) — Hélène, Victor, Nina, Gaston, the Orchestrator

One AI call, different instructions: `members.ask()` sends the same material with a different system
prompt (`personas/<name>.md` + shared `RULES`). Everything else is plain code.

- `packet.py` — what each member reads per candidate: shared core (job, Jules's assessment, full CV) +
  lens (Hélène: LinkedIn status; Victor: `career_timeline` from Oscar's record; Nina: `practical_text`
  (quotable answers) + `practical_checks` vs the role's logistics, incl. the `application` check and its
  quotable "Application: ..." line on whole-pool runs; a salary in another currency than the budget is
  "unknown", never converted). Built from `committee_in.json`, which the Node side writes with the
  practical answers of Jules's 10 finalists ONLY. `packet.names` (report only) prefers the name on the
  application, then the one Oscar found.
- `validate.py` — every member answer: ids, score range, verdict band, each quote verified against its
  source (`cv`/`practical`/`linkedin`), members kept in their lane, round-0 `top5`.
- `orchestrator.py` — round 0 alone (Hélène → Victor → Nina; Nina skipped if nothing practical), then up
  to 3 debate rounds on `contested()` candidates with `assignments()` (highest/lowest scorer, the middle one once, + Victor's
  rescue), one re-prompt on validation errors, stop on consensus / deadlock / max rounds, `rank()` =
  average score (ties: direct applicants first when the role's tie-break is on, then Jules, Remi). `Trace` writes `trace.jsonl` (prompts, raw answers) and `feed.json`. `history` (member → cid → one entry per round: score, verdict, verified arguments, concerns, question, reply, why they were called) is saved in `committee.json` for the committee page; `positions` only holds the final round.
- `gaston.py` — `confidence()` and every fact by code; one AI call for the wording; strengths' quotes
  verified; one re-prompt; `fill()` completes from the members' verified arguments; works with the AI down.
- `report.py` — `to_markdown()`; `pages/report.html` renders the same report JSON (print → PDF).
- `fake.py` — the fake members and Gaston for `LLM_BACKEND=fake`.

Progress files (`--progress path.json`) are written as `{stage, done, total}` so the Node layer can
poll and show a live progress indicator while an agent runs in the background.

## Key conventions

- **Admin vs. applicant separation**: `index.html` is the only page applicants see (role picker +
  upload form, public `/api/apply`, public `/api/roles`). Everything else — `pages/adminlobby.html`,
  `pages/roles.html`, `pages/committee.html`, and all `/api/admin/*` routes — requires a logged-in admin session.
- **Admin pages**: `adminlobby.html` (top 10 / top 5 board + live timeline; the timeline's "Applications in",
  "Job & criteria" and "Practical requirements" steps are buttons that open `<dialog>` cards over a blurred
  page), `roles.html` (the pipeline page: every input and output on one page, "Edit pipeline" in the lobby),
  `committee.html` (opened by "3 · Committee"; once Gaston's report exists: the 10 finalists, then per candidate All · Hélène · Victor · Nina · Gaston, All by default, each member's position round by round from `committee.history`; deep links `?role=&cand=&member=`), `report.html` (Gaston's report).
  The criteria, CV-section and practical-requirement editors live once in `pages/admin-forms.js`
  (`window.AdminForms`) and are used by both the lobby cards and `roles.html`: change them there.
- **Practical answers and LinkedIn PDFs never go through Oscar / Remi / Jules.** They are stored on the
  applicant record and only handed to the committee (for Jules's 10 finalists) in `pipeline.js`. The same
  goes for what someone applied for: it is a fact for the committee (Nina's lane), never a funnel score.
- **One CV pool, roles run on it.** Never tie an applicant to one role again; use `appliedFor`,
  `applicantsInScope`, `applicationFor`. Anything that depends on a role's CV sections must go through
  `reads.js` (`runsDirFor(role)`), never straight to `data/runs/`.
- **Scores, ranking, confidence and disagreements are computed by code, never by the LLM.** The LLM only
  gives each member's position and Gaston's wording; every quote it produces is verified.
- **Personal info never reaches the LLM-facing text.** `pii.py` strips it out during Oscar's segment
  stage into a separate record; Jules and Remi only ever see the de-identified text/record.
  Referees' contact details are stripped too (into a throwaway store, never the candidate's record),
  and the `references` section is always left out of the full text (`FULL_TEXT_DROP` in
  `pipeline.py`), whatever the role's keep/drop settings.
- **One agent per role at a time** (`startAgent` in `roles.js`): starting Remi while Clara/Oscar/Jules
  is running for the same role returns 409. Agents run as detached background work; the client polls
  `GET /api/admin/roles/:id/results`.
- **A pipeline run deletes every previous output first** (`OUTPUTS` in `pipeline.js`), so a stale
  shortlist, top 10 or report is never shown next to new results.
- JSON "databases" (`data/*.json`) are always written atomically: write to `<file>.tmp`, then
  `fs.renameSync` over the real file. Follow this pattern for any new persisted state.
- All Python invocations go through `pyRunner.js`'s `execFile` (never a shell) so filenames/user text
  can't be interpreted as shell commands — keep any new Python-calling code on that path.
