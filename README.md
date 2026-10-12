# Fred-s-Job-Challenge

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
