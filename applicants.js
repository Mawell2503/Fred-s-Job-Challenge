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
//     source: 'form' | 'import' | 'demo' }
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { runCvReader } from './cvRunner.js';
import { DEFAULT_RULES, getRole, rulesKey } from './rolesStore.js';
import { upgradePractical } from './practical.js';

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
    if (changed) saveApplicants();
}

// One CV at a time: keeps numbering safe and the machine responsive.
let queue = Promise.resolve();
export const inQueue = job => (queue = queue.then(job, job));

export const listApplicants = () => applicants;
export const findApplicant = candidateId => applicants.find(a => a.candidateId === candidateId) || null;
/** People who chose this role on the form. */
export const directApplicants = roleId => applicants.filter(a => a.appliedFor.includes(roleId));
/** Who a run of this role reads: the whole pool, or only its direct applicants (role.scope). */
export const applicantsInScope = role => (role?.scope === 'direct' ? directApplicants(role.id) : applicants);

/**
 * How this applicant stands towards `roleId`:
 *   { kind: 'this' }                       applied for this role
 *   { kind: 'open' }                       open application (any position)
 *   { kind: 'other', roles: ['R004'] }     applied for other roles only
 */
export function applicationFor(a, roleId) {
    if (a.appliedFor.includes(roleId)) return { kind: 'this' };
    if (!a.appliedFor.length) return { kind: 'open' };
    return { kind: 'other', roles: [...a.appliedFor] };
}

/**
 * Record one application: run Oscar on the CV (the pool's arrival read, DEFAULT_RULES) and store it.
 * `cvPath` / `linkedinPath` must already be inside UPLOAD_DIR. `roleId` null = open application.
 * Returns one of:
 *   { duplicate: true }                    this CV is already in the pool for that choice (nothing changed)
 *   { added: true, applicant }             this CV was already in the pool: `roleId` added to what they applied for
 *   { applicant, result }                  new (result.status: ok | warning)
 *   { applicant: null, result }            Oscar could not read the CV at all (status failed): NOT recorded, so
 *                                          the applicant can send another file (or the same one) at once
 * The caller deletes the uploads in every case but the new one.
 * `nameFromCv`: use the name Oscar finds in the CV, `name` only as the fallback (the import uses this).
 */
export async function submitApplication({ roleId = null, cvPath, cvName = '', linkedinPath = null, name, linkedinUrl = '',
    instagramUrl = '', practical, source = 'form', nameFromCv = false, appliedAt = null }) {
    const sha = fileHash(cvPath);
    return inQueue(async () => {
        // checked inside the queue so two identical uploads at once can't both get in
        const same = applicants.find(a => a.cvSha256 === sha);
        if (same) {
            if (!roleId || same.appliedFor.includes(roleId)) return { duplicate: true };
            same.appliedFor.push(roleId);
            saveApplicants();
            return { added: true, applicant: same };
        }
        const number = applicants.reduce((m, a) => Math.max(m, a.number), 0) + 1;
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
            practical, source,
        };
        applicants.push(applicant);
        saveApplicants();
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
