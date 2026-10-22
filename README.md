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

## Running

```
npm install
npm run dev        # http://localhost:3000
```

## Admin accounts

Admins are stored in `data/users.json` (git-ignored) with scrypt-hashed passwords. Sessions live in `data/sessions.json`.

- **From the page:** open the site, click "ADMIN LogIn", and use "Sign up". Usernames are 3-32 chars (letters, numbers, `.`, `-`, `_`, case-insensitive); passwords are 8+ chars.
- **From the terminal:** `npm run create-admin -- <username> <password>` (and `npm run create-admin -- --list`).
- **Lock sign-up down:** start with `ALLOW_SIGNUP=false` so only the CLI can create admins.

### API

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/admin/signup` | Create an admin and log in |
| POST | `/api/admin/login` | Log in (sets an httpOnly `sid` cookie) |
| POST | `/api/admin/logout` | End the session |
| GET | `/api/admin/me` | Current admin, or 401 |

Everything under `/pages` requires a logged-in admin. Set `NODE_ENV=production` to make the cookie HTTPS-only.
