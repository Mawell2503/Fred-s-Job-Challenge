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
| **Jules** | `hiring/jules.py` | Claude reads each of the 20 full CVs, criterion by criterion, with word-for-word quotes. `citation_checker.py` verifies every quote before scoring; top 5 with detailed score and list of what's missing. |

Admin page: `/pages/roles.html` (linked from the lobby). Create a role, ask Clara, check the criteria,
pick Oscar's sections, then run Remi and Jules. Applicants choose an open role on the home page.

The agents also run from the terminal:
```
python -m cv_reader cvs/ -o out                      # Oscar on a folder
python -m hiring clara --input clara_in.json --out job.json
python -m hiring remi  --job job.json --runs out --out remi.json
python -m hiring jules --job job.json --runs out --remi remi.json --out jules.json
```

## Running

```
npm install
pip install -r cv_reader/requirements.txt
cp .env.example .env   # then check the settings
npm run dev            # http://localhost:3000
```

**LLM (Clara and Jules):** by default the site uses your Claude subscription through Claude Code.
Install Claude Code on the machine running the site and sign in once by running `claude`.
To use an API key or a local Ollama model instead, change `LLM_BACKEND` in `.env`.

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
| PATCH | `/api/admin/roles/:id` | Edit title, status, job ad, criteria, keep/drop sections |
| POST | `/api/admin/roles/:id/clara`, `/oscar`, `/remi`, `/jules` | Start an agent in the background |
| GET | `/api/admin/roles/:id/results` | Agent states, progress, Remi and Jules results |
| GET | `/api/admin/applicants/:id/text`, `/full`, `/record` | Oscar's outputs for one applicant |

Everything under `/pages` requires a logged-in admin. Set `NODE_ENV=production` to make the cookie HTTPS-only.
