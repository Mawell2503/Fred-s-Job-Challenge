import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyRoutes } from './apply.js';
import {
    SESSION_TTL_MS,
    createUser,
    checkLogin,
    createSession,
    getSessionUser,
    destroySession,
} from './auth.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3000;
const IS_PROD = process.env.NODE_ENV === 'production';
// Open sign-up is handy while developing. Set ALLOW_SIGNUP=false to lock it down
// (then create admins with `npm run create-admin`).
const ALLOW_SIGNUP = process.env.ALLOW_SIGNUP !== 'false';
const COOKIE = 'sid';

app.disable('x-powered-by');
app.use(express.json({ limit: '10kb' }));

// ---------- helpers ----------
function readCookie(req, name) {
    const header = req.headers.cookie || '';
    for (const part of header.split(';')) {
        const i = part.indexOf('=');
        if (i > -1 && part.slice(0, i).trim() === name) return decodeURIComponent(part.slice(i + 1).trim());
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
    const rec = attempts.get(key);
    if (!rec || rec.reset < Date.now()) attempts.set(key, { count: 1, reset: Date.now() + WINDOW_MS });
    else rec.count++;
}

function requireAuth(req, res, next) {
    const user = getSessionUser(readCookie(req, COOKIE));
    if (!user) {
        // API callers get a 401, page loads get sent back to the login form
        if (req.path.startsWith('/api/')) return res.status(401).json({ error: 'Not logged in.' });
        return res.redirect('/#admin');
    }
    req.user = user;
    next();
}

// ---------- auth API ----------
// Sign up: validate, store a hashed password, and log the new admin straight in
app.post('/api/admin/signup', async (req, res) => {
    if (!ALLOW_SIGNUP) return res.status(403).json({ error: 'Sign-up is disabled.' });
    const result = await createUser(req.body?.user, req.body?.password);
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
app.use(applyRoutes({ requireAuth }));
// ---------- pages ----------
// Only these public files are served (not index.js, package.json, data/, .git, etc.)
const publicFile = file => (req, res) => res.sendFile(path.join(__dirname, file));
app.get('/', publicFile('index.html'));
app.get('/index.html', publicFile('index.html'));
app.get('/styles.css', publicFile('styles.css'));
app.get('/progressbar.js', publicFile('progressbar.js'));

// Everything under /pages needs a valid admin session
app.use('/pages', requireAuth, express.static(path.join(__dirname, 'pages'), { dotfiles: 'deny' }));

app.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}/`);
    console.log(`Sign-up is ${ALLOW_SIGNUP ? 'OPEN' : 'disabled'}.`);
});
