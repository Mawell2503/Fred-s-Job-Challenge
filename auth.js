// Auth storage + helpers: users (hashed passwords) and sessions, persisted as JSON in ./data
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { promisify } from 'node:util';

const scrypt = promisify(crypto.scrypt);

const DATA_DIR = path.resolve('data');
const USERS_FILE = path.join(DATA_DIR, 'users.json');
const SESSIONS_FILE = path.join(DATA_DIR, 'sessions.json');

export const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days
export const MIN_PASSWORD_LENGTH = 8;
const USERNAME_RE = /^[a-z0-9._-]{3,32}$/;

fs.mkdirSync(DATA_DIR, { recursive: true });

// ---------- tiny JSON persistence (atomic write: temp file, then rename) ----------
function load(file, fallback) {
    try {
        return JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch {
        return fallback;
    }
}

function save(file, data) {
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, file);
}

// ---------- password hashing (scrypt, built into Node: no extra dependency) ----------
// Stored format: scrypt$<salt hex>$<hash hex>
async function hashPassword(password) {
    const salt = crypto.randomBytes(16);
    const hash = await scrypt(password, salt, 64);
    return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`;
}

async function verifyPassword(password, stored) {
    const [scheme, saltHex, hashHex] = String(stored).split('$');
    if (scheme !== 'scrypt' || !saltHex || !hashHex) return false;
    const expected = Buffer.from(hashHex, 'hex');
    const actual = await scrypt(password, Buffer.from(saltHex, 'hex'), expected.length);
    return crypto.timingSafeEqual(actual, expected);
}

// Compared against when the username doesn't exist, so response time doesn't reveal valid usernames
const DUMMY_HASH = await hashPassword('dummy-password-for-timing');

// ---------- users ----------
let users = load(USERS_FILE, []);

// One-time migration of the old plaintext format [{ user, password }] to hashed passwords
let migrated = false;
for (const u of users) {
    if (u.password !== undefined && !u.passwordHash) {
        u.passwordHash = await hashPassword(u.password);
        delete u.password;
        migrated = true;
    }
    u.user = String(u.user).toLowerCase();
}
if (migrated) {
    save(USERS_FILE, users);
    console.log('Migrated plaintext passwords in data/users.json to hashes.');
}

export function normalizeUsername(raw) {
    return String(raw ?? '').trim().toLowerCase();
}

export function validateCredentials(user, password) {
    if (!USERNAME_RE.test(user)) {
        return 'Username must be 3-32 characters: letters, numbers, dot, dash or underscore.';
    }
    if (typeof password !== 'string' || password.length < MIN_PASSWORD_LENGTH) {
        return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
    }
    if (password.length > 200) return 'Password is too long.';
    return null;
}

export async function createUser(rawUser, password) {
    const user = normalizeUsername(rawUser);
    const problem = validateCredentials(user, password);
    if (problem) return { error: problem, status: 400 };
    if (users.some(u => u.user === user)) return { error: 'Username already taken.', status: 409 };

    const record = {
        id: crypto.randomUUID(),
        user,
        passwordHash: await hashPassword(password),
        createdAt: new Date().toISOString(),
    };
    users.push(record);
    save(USERS_FILE, users);
    return { user: record };
}

export async function checkLogin(rawUser, password) {
    const user = normalizeUsername(rawUser);
    const record = users.find(u => u.user === user);
    const ok = await verifyPassword(String(password ?? ''), record ? record.passwordHash : DUMMY_HASH);
    return record && ok ? record : null;
}

export const userCount = () => users.length;

export function listUsers() {
    return users.map(({ id, createdAt, user }) => ({ id, createdAt, user }));
}

// ---------- sessions (token in an httpOnly cookie; only a hash of it is stored on disk) ----------
let sessions = load(SESSIONS_FILE, {});

const sha256 = s => crypto.createHash('sha256').update(s).digest('hex');

function pruneSessions() {
    const now = Date.now();
    let changed = false;
    for (const [key, s] of Object.entries(sessions)) {
        if (s.expires < now) {
            delete sessions[key];
            changed = true;
        }
    }
    if (changed) save(SESSIONS_FILE, sessions);
}
pruneSessions();

export function createSession(userRecord) {
    const token = crypto.randomBytes(32).toString('hex');
    sessions[sha256(token)] = { user: userRecord.user, expires: Date.now() + SESSION_TTL_MS };
    save(SESSIONS_FILE, sessions);
    return token;
}

export function getSessionUser(token) {
    if (!token) return null;
    const key = sha256(token);
    const s = sessions[key];
    if (!s) return null;
    if (s.expires < Date.now()) {
        delete sessions[key];
        save(SESSIONS_FILE, sessions);
        return null;
    }
    // Drop sessions for users that were deleted from users.json
    return users.some(u => u.user === s.user) ? s.user : null;
}

export function destroySession(token) {
    if (!token) return;
    const key = sha256(token);
    if (sessions[key]) {
        delete sessions[key];
        save(SESSIONS_FILE, sessions);
    }
}
