# Fred-s-Job-Challenge

## EndGoal
0.1 Main> System that "cleans" CVs formats.
0.2 Main> AI agents that can read the "cleaned" CVs and give opinions back while sorting them out(200>50>20>10/5best)
0.3 Main> A visual representation of whats being done.
0.4> ???....

## Description of my take on the challenge
Seperating admins from CV applicants
- `index.html` > only page the applicants can see
- the rest of the site can only be accessed by admins through admin log in/sign up.

Sorting/Cleaning of the CVs to bring it down to a text block that AIs can easily go through
    - `cv_reader` has the whole logic
    - `loaders\__init__.py` holds stage 1 and 2 > Loading CV and Extracting
    - `normalize.py` cleans up the extractions/stage 3
    - `segment.py` sorts the extraction /stage 4
    - `rules\engine.py` applies the rules/stage 5
    - `assemble.py` groups all the saved extracted information back into one single block/stage 6
    - `structure.py` builds a structured record per CV (jobs + dates, total years, skills, languages, availability)/stage 7
    - `vocab/synonyms.yaml` standardises vocabulary ("ReactJS", "dev front" -> one skill); edit it freely
    - `embed.py` computes each profile's embedding (the "meaning" fingerprint Remi compares)

## The hiring pipeline (page 12 of the challenge)

| Agent | Where | What it does |
| --- | --- | --- |
| **Oscar** | `cv_reader/` | CV -> clean text (kept sections), full text without personal details, structured record, embedding. Personal details go to a separate file. Runs on every upload with the role's keep/drop settings. |
| **Clara** | `hiring/clara.py` | Job ad and/or the admin's lists -> must-haves + nice-to-haves (with keywords). The admin reviews and saves them. |
| **Remi** | `hiring/remi.py` | Must-have gate (PASS / FAIL / UNKNOWN; FAIL eliminates, UNKNOWN is only penalised), then ranks by keywords + meaning + nice-to-haves and keeps the top 20. Plain code; an optional Claude double-check exists. |
| **Jules** | `hiring/jules.py` | Claude reads each of the 20 full CVs, criterion by criterion, with word-for-word quotes. `citation_checker.py` verifies every quote before scoring; keeps the **top 10** with detailed score and list of what's missing. |

## The hiring committee (stage 2)

Jules's top 10 go to a committee that debates and picks the final **top 5**. The members are **not three AIs**:
they are three instructions for the **same** AI call (`hiring/committee/members.py`). The material stays the
same, only the system prompt changes (`hiring/committee/personas/*.md`, plain text, edit freely).

| Member | Looks at | Character | Underestimates |
| --- | --- | --- | --- |
| **Hélène**, the engineer | technical solidity, concrete achievements, the LinkedIn profile | dry, distrustful, only trusts proof | soft skills, learning fast |
| **Victor**, the veteran | career path, reliability, teamwork (+ a career timeline computed by code) | calm, contrarian; defends career changers, rescues those Hélène dismissed | real technical level |
| **Nina**, operations | availability, mobility, budget, language (+ the practical answers and checks) | rushed, blunt, allergic to theory | mid-term potential |
| **Gaston**, the clerk | everything | doesn't speak, doesn't vote | writes the report |
| **The Orchestrator** | plain code (`orchestrator.py`) | launches the members in order, chooses who speaks, stops after 3 rounds, re-prompts nonsense, keeps the trace | |

How a run goes:
1. **Round 0**: Hélène, Victor and Nina each score the 10 alone (these are the "positions before").
2. **Debate (max 3 rounds)**: only on contested candidates (scores 3+ apart, "advance" next to "reject", or
   close to the top-5 cut-off), mostly with the members in conflict (highest and lowest score; a member in
   between is heard once, the first round the candidate is contested); Victor is always called when
   Hélène gave 3 or less to someone another member gave 6+. Stops early at consensus or when nobody moves.
3. Every answer is checked by code: every quote must be found word for word in the CV / practical answers,
   scores and verdicts must agree, members must stay in their lane. A bad answer gets **one** re-prompt.
4. **Top 5 = average of the members' final scores** (ties: Jules, then Remi). Plain code.
5. **Gaston** writes the report. Code computes the facts (header, scores, disagreements, confidence
   High/Medium/Low with its reasons, the before/after appendix); the AI only writes the words, and every
   quoted strength is checked again.

Report sections: header (assignment, date, number of applications) · top 5 with score and comment ·
per candidate 3 substantiated strengths, 2 areas for improvement, what's missing · unresolved
disagreements · recommendation with confidence level · interview questions · appendix (positions
before and after the debate). Shown on the admin page, at `/pages/report.html?role=R001`
(print / save as PDF) and as `report.md`.

**What feeds Nina and Hélène never goes through the funnel.** Applicants fill in required *practical
details* on the form (salary expectation in the role's currency, start date, location, relocation,
travel, way of working, right to work, languages with level) and may upload their **LinkedIn profile
PDF** (optional; "Resources → Save to PDF" on LinkedIn). Both are stored on the side; Oscar, Remi and
Jules never see them. Only once Jules has picked the top 10 are those 10 candidates' answers handed to the
committee. The admin sets the role's *practical requirements* (budget, client country, start date,
required languages...) in the "Practical requirements" card. *The LinkedIn PDF parser is not built yet:
Hélène is told whether a profile was provided and judges from the CV.*

Admin lobby: `/pages/adminlobby.html`. Its timeline opens cards (they turn blue on hover): **Applications
in** (every application as submitted: name, CV file, practical details), **Job & criteria** (profession, job
ad, your must-haves / nice-to-haves → Ask Clara → Load into the editor → check the criteria → Oscar's CV
sections → Save criteria) and **Practical requirements** (for Nina). **3 · Committee** opens the committee
page (`/pages/committee.html`, still to be designed; links to Gaston's report).

Pipeline page: `/pages/roles.html` (**Edit pipeline** in the lobby), everything on one page. Create a role,
ask Clara and check her criteria (the one manual step), set the practical requirements, then press **Run
full pipeline**: Oscar (only CVs read with older section settings), Remi, Jules, the committee and Gaston run
one after the other in the background, with live progress and a live debate feed. Applicants choose an open
role on the home page.

The agents also run from the terminal:
```
python -m cv_reader cvs/ -o out                      # Oscar on a folder
python -m hiring clara --input clara_in.json --out job.json
python -m hiring remi  --job job.json --runs out --out remi.json
python -m hiring jules --job job.json --runs out --remi remi.json --out jules.json
python -m hiring committee --job job.json --runs out --jules jules.json --input committee_in.json \
                           --out committee.json --report report.md --trace trace.jsonl
npm run pipeline -- R001   # the whole pipeline for one role, like the button (stop the server first)
```

## Demo and tests

```
npm run demo               # a demo role + 15 fictional, deliberately contentious applications (demo/)
npm run pipeline -- R00x   # run everything on it (the id is printed by the previous command)
npm test                   # Node + Python tests: no AI, no cost
```
With `LLM_BACKEND=fake` everything runs offline in seconds: plain rules stand in for the AI, to check the
plumbing. Switch to `claude_cli` or `api` for the real agents.

## Running

```
npm install
pip install -r cv_reader/requirements.txt
cp .env.example .env   # then check the settings
npm run dev            # http://localhost:3000
```

**LLM (Clara, Jules, the committee, Gaston):** by default the site uses your Claude subscription through
Claude Code. Install Claude Code on the machine running the site and sign in once by running `claude`.
To use an API key or a local Ollama model instead, change `LLM_BACKEND` in `.env` (`fake` = no AI, for
tests and offline demos). A committee run makes about 8-16 AI calls; the temperature setting (0.3) only
applies to the `api` and `ollama` backends.

## Admin accounts

Admins are stored in `data/users.json` (git-ignored) with scrypt-hashed passwords. Sessions live in `data/sessions.json`.

- **First admin, from the page:** on a fresh install, click "Admin login" and use "Sign up". After that first account exists, sign-up closes. Usernames are 3-32 chars (letters, numbers, `.`, `-`, `_`, case-insensitive); passwords are 8+ chars.
- **More admins, from the terminal:** `npm run create-admin -- <username> <password>` (and `npm run create-admin -- --list`).
- **Leave sign-up open** (e.g. while developing): `ALLOW_SIGNUP=true` in `.env`.

The same CV file sent twice for the same position is refused (409); it is recognised by a fingerprint of its content.

### API

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/admin/signup` | Create an admin and log in |
| POST | `/api/admin/login` | Log in (sets an httpOnly `sid` cookie) |
| POST | `/api/admin/logout` | End the session |
| GET | `/api/admin/me` | Current admin, or 401 |
| GET | `/api/roles` | Open roles (public, for the application form) |
| GET / POST | `/api/admin/roles` | List / create roles |
| PATCH | `/api/admin/roles/:id` | Edit title, status, job ad, criteria, keep/drop sections, practical requirements |
| POST | `/api/admin/roles/:id/clara` | Clara proposes criteria (background) |
| POST | `/api/admin/roles/:id/pipeline` | Oscar → Remi → Jules → committee → Gaston (background) |
| GET | `/api/admin/roles/:id/results` | Step states, progress, Remi, Jules, committee results, live debate feed |
| GET | `/api/admin/roles/:id/report.md` | Gaston's report (Markdown download) |
| GET | `/api/admin/applicants?role=R001[&details=1]` | A role's applicants (`details=1`: with their practical answers) |
| GET | `/api/admin/applicants/:id/text`, `/full`, `/record`, `/practical`, `/linkedin` | One applicant's files |
| GET | `/api/admin/applicants/:id/cv` | The CV file exactly as uploaded (PDF/TXT open, DOCX downloads) |

Everything under `/pages` requires a logged-in admin. Set `NODE_ENV=production` to make the cookie HTTPS-only.
