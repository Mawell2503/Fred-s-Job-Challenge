// CV batches: every CV in the pool belongs to exactly ONE batch (applicant.batch = batch id).
// No express import, and no import of applicants.js (applicants.js imports this file).
//
//   kind 'initial'  "Batch 0": every CV that was in the pool before batches existed (made once, on start)
//   kind 'weekly'   the public form's CVs, one batch per Monday-to-Sunday week (made on the first arrival)
//   kind 'upload'   a batch an admin named and uploaded on the pipeline page (also `npm run import`, the demo)
//
// data/batches.json:
//   { settings: { reviewAfterDays: 7 },          the weekly question comes this many days after the week ends
//     lastNumber: 57,                             the highest candidate number ever given: a deleted CV's
//                                                 number is never given again (old reports keep pointing at
//                                                 the right person)
//     batches: [ { id: 'B003', name, kind, createdAt, weekStart?: '2026-11-02',
//                  decision: null | 'kept',       a weekly batch someone chose to keep (no more questions)
//                  askAfter: null | iso } ] }     "Ask me later": not before this date
//
// Archiving and deleting (which touch the applicants too) live in batchOps.js.
import fs from 'node:fs';
import path from 'node:path';

const DATA_DIR = path.resolve('data');
const FILE = path.join(DATA_DIR, 'batches.json');
export const REVIEW_CHOICES = [7, 14, 30];
export const KINDS = ['initial', 'weekly', 'upload'];
const DAY = 864e5;

fs.mkdirSync(DATA_DIR, { recursive: true });
let state = null;
try { state = JSON.parse(fs.readFileSync(FILE, 'utf8')); } catch { /* first run */ }
if (!state || !Array.isArray(state.batches)) state = { settings: {}, lastNumber: 0, batches: [] };
state.settings ||= {};
if (!REVIEW_CHOICES.includes(state.settings.reviewAfterDays)) state.settings.reviewAfterDays = 7;
state.lastNumber = Number(state.lastNumber) || 0;

export function saveBatches() {
    const tmp = `${FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(state, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, FILE);
}

export const listBatches = () => state.batches;
export const getBatch = id => state.batches.find(b => b.id === id) || null;
export const settings = () => state.settings;

// ---------------- candidate numbers: never given twice ----------------
/** The next candidate number: above every number in the pool AND every number ever given. */
export function nextNumber(applicants) {
    return Math.max(state.lastNumber, ...applicants.map(a => a.number || 0), 0) + 1;
}
/** Remember a number as given (call once the CV is really recorded). */
export function noteNumber(n) {
    if (n > state.lastNumber) { state.lastNumber = n; saveBatches(); }
}

// ---------------- making batches ----------------
export const cleanName = v => String(v ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80);

function newId() {
    const n = state.batches.reduce((m, b) => Math.max(m, Number(b.id.slice(1)) || 0), -1) + 1;
    return `B${String(n).padStart(3, '0')}`;
}

function add(fields) {
    const batch = { id: newId(), createdAt: new Date().toISOString(), decision: null, askAfter: null, ...fields };
    state.batches.push(batch);
    saveBatches();
    return batch;
}

/** Batch 0 (made once, on the first start with batches). */
export function initialBatch() {
    return state.batches.find(b => b.kind === 'initial') || add({ name: 'Batch 0 · before batches', kind: 'initial' });
}

/** A new batch an admin names (the pipeline page, `npm run import`, the demo). Names are unique. */
export function createBatch(name) {
    const clean = cleanName(name);
    if (!clean) return { error: 'Give the batch a name.' };
    if (state.batches.some(b => b.name.toLowerCase() === clean.toLowerCase())) return { error: `There is already a batch called "${clean}".` };
    return { batch: add({ name: clean, kind: 'upload' }) };
}
/** The batch with this name, made if it doesn't exist yet (scripts: re-running them fills the same batch). */
export function batchNamed(name) {
    const clean = cleanName(name);
    return state.batches.find(b => b.name.toLowerCase() === clean.toLowerCase()) || add({ name: clean, kind: 'upload' });
}

export function renameBatch(id, name) {
    const b = getBatch(id);
    if (!b) return { error: 'Unknown batch.' };
    const clean = cleanName(name);
    if (!clean) return { error: 'Give the batch a name.' };
    if (state.batches.some(x => x !== b && x.name.toLowerCase() === clean.toLowerCase())) return { error: `There is already a batch called "${clean}".` };
    b.name = clean;
    saveBatches();
    return { batch: b };
}

export function removeBatch(id) {
    const i = state.batches.findIndex(b => b.id === id);
    if (i >= 0) { state.batches.splice(i, 1); saveBatches(); }
}

// ---------------- weekly batches ----------------
/** Monday of the week `date` falls in, as 'YYYY-MM-DD' (the server's local calendar). */
export function weekStartOf(date = new Date()) {
    const d = new Date(date);
    d.setHours(12, 0, 0, 0);                                   // noon: no daylight-saving edge cases
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const weekName = start => `Week of ${new Date(`${start}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}`;

/** The weekly batch for the week `date` falls in, made on the first CV of that week. */
export function weeklyBatch(date = new Date()) {
    const start = weekStartOf(date);
    return state.batches.find(b => b.kind === 'weekly' && b.weekStart === start)
        || add({ name: weekName(start), kind: 'weekly', weekStart: start });
}

// ---------------- the weekly question: keep, archive or delete? ----------------
/** When a weekly batch's question is due: the Monday after its week + reviewAfterDays. */
export function dueAt(b) {
    if (b.kind !== 'weekly') return null;
    return new Date(new Date(`${b.weekStart}T00:00:00`).getTime() + (7 + state.settings.reviewAfterDays) * DAY);
}
/** Weekly batches waiting for an answer (`activeCount(b)` = its CVs that are not archived). */
export function dueBatches(activeCount, now = new Date()) {
    return state.batches.filter(b => b.kind === 'weekly' && !b.decision && activeCount(b) > 0
        && dueAt(b) <= now && (!b.askAfter || new Date(b.askAfter) <= now));
}
export function keepBatch(id) {
    const b = getBatch(id);
    if (b) { b.decision = 'kept'; b.askAfter = null; saveBatches(); }
    return b;
}
/** "Ask me later": the question comes back in a week. */
export function snoozeBatch(id, now = new Date()) {
    const b = getBatch(id);
    if (b) { b.askAfter = new Date(now.getTime() + 7 * DAY).toISOString(); saveBatches(); }
    return b;
}
export function setReviewAfterDays(days) {
    if (!REVIEW_CHOICES.includes(days)) return { error: `Choose ${REVIEW_CHOICES.join(', ')} days.` };
    state.settings.reviewAfterDays = days;
    saveBatches();
    return { settings: state.settings };
}
