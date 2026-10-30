import './root.js';                // run from this folder, wherever the server is started from
import './env.js';                 // .env settings (LLM_BACKEND, PYTHON_BIN, ...) before anything else
import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyRoutes } from './apply.js';
import { roleRoutes } from './roles.js';
import { batchRoutes } from './batchRoutes.js';
import {
    SESSION_TTL_MS,
    createUser,
    checkLogin,
    createSession,
    getSessionUser,
    destroySession,
    userCount,
} from './auth.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3000;
const IS_PROD = process.env.NODE_ENV === 'production';
// Sign-up is closed by default: every admin can read every CV and contact detail.
// The first admin can always sign up (nothing to protect yet); after that, create admins
// with `npm run create-admin`, or set ALLOW_SIGNUP=true to leave the form open.
const ALLOW_SIGNUP = process.env.ALLOW_SIGNUP === 'true';
const signupOpen = () => ALLOW_SIGNUP || userCount() === 0;
let signupBusy = false;
const COOKIE = 'sid';

app.disable('x-powered-by');
const smallJson = express.json({ limit: '10kb' });
// role and batch routes parse their own (larger) bodies: job descriptions can be long, and archiving or
// deleting CVs sends their ids (thousands of them for a big batch)
const ownBodies = ['/api/admin/roles', '/api/admin/cvs', '/api/admin/batches'];
app.use((req, res, next) => (ownBodies.some(p => req.path.startsWith(p)) ? next() : smallJson(req, res, next)));

// ---------- helpers ----------
function readCookie(req, name) {
    const header = req.headers.cookie || '';
    for (const part of header.split(';')) {
        const i = part.indexOf('=');
        if (i > -1 && part.slice(0, i).trim() === name) {
            // a malformed value ("%E0%A4%A") is simply no session, never a server error
            try { return decodeURIComponent(part.slice(i + 1).trim()); } catch { return null; }
        }
    }
    return null;
}

function setSessionCookie(res, token) {
    res.cookie(COOKIE, token, {
        httpOnly: true,
        sameSite: 'lax',
        secure: IS_PROD,
        maxAge: SESSION_TTL_MS,
        path: '/',
    });
}

const attempts = new Map();
const WINDOW_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 10;

function tooManyAttempts(key) {
    const rec = attempts.get(key);
    if (!rec || rec.reset < Date.now()) return false;
    return rec.count >= MAX_ATTEMPTS;
}

function recordFailure(key) {
    // forget expired windows now and then, so random usernames can't grow this map forever
    if (attempts.size > 5000) for (const [k, r] of attempts) if (r.reset < Date.now()) attempts.delete(k);
    const rec = attempts.get(key);
    if (!rec || rec.reset < Date.now()) attempts.set(key, { count: 1, reset: Date.now() + WINDOW_MS });
    else rec.count++;
}

function requireAuth(req, res, next) {
    const user = getSessionUser(readCookie(req, COOKIE));
    if (!user) {
        // API callers get a 401, page loads get sent back to the login form
        if (req.originalUrl.startsWith('/api/')) return res.status(401).json({ error: 'Not logged in.' });
        return res.redirect('/#admin');
    }
    req.user = user;
    next();
}

// ---------- auth API ----------
// Sign up: validate, store a hashed password, and log the new admin straight in
app.post('/api/admin/signup', async (req, res) => {
    // one sign-up at a time, so two "first admin" requests can't both slip through
    if (!signupOpen()) {
        return res.status(403).json({ error: 'Sign-up is closed. Ask an existing admin to create your account.' });
    }
    if (signupBusy) return res.status(429).json({ error: 'Another sign-up is in progress. Try again in a moment.' });
    signupBusy = true;
    let result;
    try { result = await createUser(req.body?.user, req.body?.password); } finally { signupBusy = false; }
    if (result.error) return res.status(result.status).json({ error: result.error });
    setSessionCookie(res, createSession(result.user));
    res.status(201).json({ user: result.user.user });
});

// Log in: check credentials, start a session
app.post('/api/admin/login', async (req, res) => {
    const name = String(req.body?.user ?? '').trim().toLowerCase();
    const key = `${req.ip}|${name}`;
    if (tooManyAttempts(key)) {
        return res.status(429).json({ error: 'Too many attempts. Try again in a few minutes.' });
    }
    const record = await checkLogin(name, req.body?.password);
    if (!record) {
        recordFailure(key);
        return res.status(401).json({ error: 'Incorrect username or password.' });
    }
    attempts.delete(key);
    setSessionCookie(res, createSession(record));
    res.json({ user: record.user });
});

// Log out: invalidate the session and clear the cookie
app.post('/api/admin/logout', (req, res) => {
    destroySession(readCookie(req, COOKIE));
    res.clearCookie(COOKIE, { path: '/' });
    res.json({ ok: true });
});

// Who am I? Useful for admin pages to check they're logged in
app.get('/api/admin/me', requireAuth, (req, res) => res.json({ user: req.user }));
app.use(roleRoutes({ requireAuth }));
app.use(applyRoutes({ requireAuth }));
app.use(batchRoutes({ requireAuth }));
// ---------- pages ----------
// Only these public files are served (not index.js, package.json, data/, .git, etc.)
const publicFile = file => (req, res) => res.sendFile(path.join(__dirname, file));
app.get('/', publicFile('index.html'));
app.get('/index.html', publicFile('index.html'));
app.get('/styles.css', publicFile('styles.css'));

// Everything under /pages needs a valid admin session
app.use('/pages', requireAuth, express.static(path.join(__dirname, 'pages'), { dotfiles: 'deny' }));

// Anything that reaches here failed: a JSON body that does not parse, or an unexpected error.
// API callers get JSON, never Express's HTML page with the server's stack trace in it.
app.use((err, req, res, next) => {
    if (res.headersSent) return next(err);
    const status = err.status >= 400 && err.status < 500 ? err.status : 500;
    if (status === 500) console.error(err);
    const error = status === 500 ? 'Something went wrong. Please try again.' : 'Invalid request.';
    if (req.originalUrl.startsWith('/api/')) return res.status(status).json({ error });
    res.status(status).type('text/plain').send(error);
});

app.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}/`);
    console.log(ALLOW_SIGNUP ? 'Sign-up is OPEN (ALLOW_SIGNUP=true).'
        : userCount() === 0 ? 'No admin yet: the first sign-up creates one, then sign-up closes.'
            : 'Sign-up is closed (create admins with `npm run create-admin`).');
});
