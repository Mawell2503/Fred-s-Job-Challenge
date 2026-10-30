// Applicants store: the CV POOL (data/applicants.json) + the CV processing queue.
// No express import, so the website (apply.js), the pipeline runner (pipeline.js) and the
// scripts all share the same code.
//
// Every CV that arrives goes into one pool. Applicants do not belong to a role: a role is RUN on the
// pool (or only on the people who applied for it, see role.scope). What the applicant chose on the
// form is kept as a label:
//   appliedFor: []                -> an open application ("any position")
//   appliedFor: ['R001', 'R004']  -> applied for those roles (the same CV sent again for another role
//                                    adds that role here instead of creating a second applicant)
//
// One applicant record:
//   { number, candidateId: "C0007", appliedFor: [roleIds], name, linkedinUrl, instagramUrl, appliedAt,
//     cvFile, cvName, cvSha256, cvStatus, cvCodes, rulesKey, <- the CV and its arrival read by Oscar
//                                                            (cvName: original file name; rulesKey: the
//                                                            sections it was read with, see DEFAULT_RULES)
//     linkedinFile,                                          <- optional LinkedIn PDF (for Helene; parser to come)
//     practical: { salary, available_from, ... },            <- for Nina only, never through the funnel
//     source: 'form' | 'import' | 'demo' | 'batch',          <- 'batch': uploaded into a named batch (no form)
//     batch: 'B003',                                         <- the ONE batch it belongs to (batches.js)
//     archived: true }                                       <- archived: out of every run and list, restorable
//
// Archived CVs stay in this file (and their files and reads stay on disk) but are not "active": runs,
// counts and the applicant lists only see active ones. Deleting is in batchOps.js.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { runCvReader } from './cvRunner.js';
import { DEFAULT_RULES, getRole, rulesKey } from './rolesStore.js';
import { upgradePractical } from './practical.js';
import { getBatch, initialBatch, listBatches, nextNumber, noteNumber, weeklyBatch } from './batches.js';

export const DATA_DIR = path.resolve('data');
export const UPLOAD_DIR = path.join(DATA_DIR, 'uploads');
const APPLICANTS_FILE = path.join(DATA_DIR, 'applicants.json');
fs.mkdirSync(UPLOAD_DIR, { recursive: true });

let applicants = [];
try { applicants = JSON.parse(fs.readFileSync(APPLICANTS_FILE, 'utf8')); } catch { /* first run */ }

export function saveApplicants() {
    const tmp = `${APPLICANTS_FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(applicants), { mode: 0o600 });   // compact: thousands of records
    fs.renameSync(tmp, APPLICANTS_FILE);
}

/** sha256 of a file's bytes: the same CV sent twice has the same fingerprint. */
export const fileHash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

export { rulesKey };
export const DEFAULT_KEY = rulesKey(DEFAULT_RULES);

/**
 * The file name the applicant's CV had on their computer, safe to show and to send back as a download
 * name: no folders, no control or quote characters, the stored file's extension. Falls back to "CV.<ext>".
 */
export function cleanFileName(name, storedPath) {
    const ext = path.extname(storedPath || '').toLowerCase();
    let base = path.basename(String(name ?? '').replace(/\\/g, '/'));
    base = base.replace(/[\u0000-\u001f\u007f"<>|*?:]/g, '').trim().slice(0, 120);
    if (!base || base.startsWith('.')) base = `CV${ext}`;
    if (ext && path.extname(base).toLowerCase() !== ext) base += ext;
    return base;
}

// Records saved before the pool: fingerprints, and `roleId` (one role) becomes `appliedFor` (a list),
// and "right to work in the country of this position" becomes the countries they can work in.
{
    let changed = false;
    for (const a of applicants) {
        if (!a.cvSha256) {
            try { a.cvSha256 = fileHash(path.join(UPLOAD_DIR, a.cvFile)); changed = true; } catch { /* upload gone */ }
        }
        if (!Array.isArray(a.appliedFor)) {
            a.appliedFor = a.roleId ? [a.roleId] : [];
            delete a.roleId;
            changed = true;
        }
        if (upgradePractical(a.practical, getRole(a.appliedFor[0]))) changed = true;
    }
    // batches: on the first start with them, every CV goes into Batch 0 (and any CV whose batch is gone)
    const known = new Set(listBatches().map(b => b.id));
    const homeless = applicants.filter(a => !known.has(a.batch));
    if (homeless.length) {
        const b0 = initialBatch();
        for (const a of homeless) a.batch = b0.id;
        changed = true;
    }
    for (const a of applicants) noteNumber(a.number || 0);
    if (changed) saveApplicants();
}

// One CV at a time: keeps numbering safe and the machine responsive.
let queue = Promise.resolve();
export const inQueue = job => (queue = queue.then(job, job));

/** Every record, archived ones included (duplicate checks, scripts). */
export const listApplicants = () => applicants;
/** Take these records out of the pool for good (batchOps.deleteCvs removes their files first). */
export function removeApplicants(candidateIds) {
    const gone = new Set(candidateIds);
    const before = applicants.length;
    for (let i = applicants.length - 1; i >= 0; i--) if (gone.has(applicants[i].candidateId)) applicants.splice(i, 1);
    if (applicants.length !== before) saveApplicants();
    return before - applicants.length;
}
/** The CVs that count: everything not archived. */
export const activeApplicants = () => applicants.filter(a => !a.archived);
export const findApplicant = candidateId => applicants.find(a => a.candidateId === candidateId) || null;
/** People who chose this role on the form (archived ones left out). */
export const directApplicants = roleId => applicants.filter(a => !a.archived && a.appliedFor.includes(roleId));
/**
 * Who a run of this role reads (role.scope), archived CVs never included:
 *   'pool'     the whole CV pool
 *   'direct'   only the people who applied for this role
 *   'batches'  only the CVs in the batches ticked in role.batches
 */
export function applicantsInScope(role) {
    if (role?.scope === 'direct') return directApplicants(role.id);
    if (role?.scope === 'batches') {
        const chosen = new Set(role.batches || []);
        return applicants.filter(a => !a.archived && chosen.has(a.batch));
    }
    return activeApplicants();
}

/**
 * How this applicant stands towards `roleId`:
 *   { kind: 'this' }                       applied for this role
 *   { kind: 'open' }                       open application (any position)
 *   { kind: 'other', roles: ['R004'] }     applied for other roles only
 */
export function applicationFor(a, roleId) {
    if (a.appliedFor.includes(roleId)) return { kind: 'this' };
    // uploaded by an admin into a named batch: no application form, so not an "open application" either
    if (!a.appliedFor.length && a.source === 'batch') return { kind: 'batch', batch: getBatch(a.batch)?.name || 'a batch' };
    if (!a.appliedFor.length) return { kind: 'open' };
    return { kind: 'other', roles: [...a.appliedFor] };
}

/**
 * Record one application: run Oscar on the CV (the pool's arrival read, DEFAULT_RULES) and store it.
 * `cvPath` / `linkedinPath` must already be inside UPLOAD_DIR. `roleId` null = open application.
 * Returns one of:
 *   { duplicate: true }                    this CV is already in the pool for that choice (nothing changed)
 *   { added: true, applicant }             this CV was already in the pool: `roleId` added to what they applied for
 *   { restored: true, applicant }          this CV was ARCHIVED: it is active again, in this upload's batch
 *   { moved: true, from, applicant }       (`batchId` given) the CV was in another batch: it now belongs to this one
 *   { applicant, result }                  new (result.status: ok | warning)
 *   { applicant: null, result }            Oscar could not read the CV at all (status failed): NOT recorded, so
 *                                          the applicant can send another file (or the same one) at once
 * The caller deletes the uploads in every case but the new one.
 * `nameFromCv`: use the name Oscar finds in the CV, `name` only as the fallback (the import uses this).
 * `batchId`: the batch it goes into (an upload); none = this week's batch (the public form).
 */
export async function submitApplication({ roleId = null, cvPath, cvName = '', linkedinPath = null, name, linkedinUrl = '',
    instagramUrl = '', practical, source = 'form', nameFromCv = false, appliedAt = null, batchId = null }) {
    const sha = fileHash(cvPath);
    return inQueue(async () => {
        const batchFor = () => batchId || weeklyBatch(appliedAt ? new Date(appliedAt) : new Date()).id;
        // checked inside the queue so two identical uploads at once can't both get in
        const same = applicants.find(a => a.cvSha256 === sha);
        if (same) {
            const addsRole = Boolean(roleId) && !same.appliedFor.includes(roleId);
            if (addsRole) same.appliedFor.push(roleId);
            if (same.archived) {                        // sent again: back from the archive, into this batch
                delete same.archived;
                same.batch = batchFor();
                saveApplicants();
                return { restored: true, applicant: same };
            }
            if (batchId && same.batch !== batchId) {    // uploaded into a batch: it belongs to that batch now
                const from = getBatch(same.batch)?.name || same.batch;
                same.batch = batchId;
                saveApplicants();
                return { moved: true, from, applicant: same };
            }
            if (!addsRole) return { duplicate: true };
            saveApplicants();
            return { added: true, applicant: same };
        }
        const number = nextNumber(applicants);
        const r = await runCvReader(cvPath, number, DEFAULT_RULES);
        if (r.status === 'failed') {
            fs.rmSync(r.runDir, { recursive: true, force: true });     // the number is free for the next applicant
            return { applicant: null, result: r };
        }
        const applicant = {
            number, candidateId: r.candidateId, appliedFor: roleId ? [roleId] : [],
            name: (nameFromCv && r.name) || name, instagramUrl, linkedinUrl,
            appliedAt: appliedAt || new Date().toISOString(),
            cvStatus: r.status, cvCodes: r.codes,
            cvFile: path.basename(cvPath), cvName: cleanFileName(cvName, cvPath), cvSha256: sha, rulesKey: DEFAULT_KEY,
            linkedinFile: linkedinPath ? path.basename(linkedinPath) : null,
            practical, source, batch: batchFor(),
        };
        applicants.push(applicant);
        saveApplicants();
        noteNumber(number);                             // never given again, even if this CV is deleted later
        return { applicant, result: r };
    });
}

/**
 * Bring the pool's arrival reads up to date: CVs read with older default sections are read again.
 * Runs through the queue, one CV at a time; each CV is checked again inside the queue, so two roles
 * refreshing at once never read the same CV twice.
 */
export async function refreshDefaultReads(list, onProgress) {
    const stale = list.filter(a => a.rulesKey !== DEFAULT_KEY);
    let done = 0, failed = 0;
    for (const a of stale) {
        await inQueue(async () => {
            if (a.rulesKey === DEFAULT_KEY) return;
            try {
                const r = await runCvReader(path.join(UPLOAD_DIR, a.cvFile), a.number, DEFAULT_RULES);
                Object.assign(a, { cvStatus: r.status, cvCodes: r.codes, rulesKey: DEFAULT_KEY, processedAt: new Date().toISOString() });
                if (r.status === 'failed') failed++;
            } catch (e) {
                failed++;
                Object.assign(a, { cvStatus: 'failed', cvCodes: ['REPROCESS_ERROR'] });
                console.error(`re-read ${a.candidateId}:`, e.message);
            }
            saveApplicants();
        });
        onProgress?.(++done, stale.length);
    }
    return { total: stale.length, failed };
}
