// Applicants store: data/applicants.json + the CV processing queue.
// No express import, so the website (apply.js), the pipeline runner (pipeline.js) and the
// scripts all share the same code.
//
// One applicant record:
//   { number, candidateId: "C0007", roleId, name, linkedinUrl, instagramUrl, appliedAt,
//     cvFile, cvSha256, cvStatus, cvCodes, rulesKey,         <- the CV, through Oscar
//     linkedinFile,                                          <- optional LinkedIn PDF (for Helene; parser to come)
//     practical: { salary, available_from, ... } }           <- for Nina only, never through the funnel
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { runCvReader } from './cvRunner.js';

export const DATA_DIR = path.resolve('data');
export const UPLOAD_DIR = path.join(DATA_DIR, 'uploads');
const APPLICANTS_FILE = path.join(DATA_DIR, 'applicants.json');
fs.mkdirSync(UPLOAD_DIR, { recursive: true });

let applicants = [];
try { applicants = JSON.parse(fs.readFileSync(APPLICANTS_FILE, 'utf8')); } catch { /* first run */ }

export function saveApplicants() {
    const tmp = `${APPLICANTS_FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(applicants, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, APPLICANTS_FILE);
}

/** sha256 of a file's bytes: the same CV sent twice has the same fingerprint. */
export const fileHash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

/** The keep/drop settings a CV was processed with, as a comparable string. */
export const rulesKey = rules => JSON.stringify(Object.entries(rules?.sections || {}).sort());

// Applicants saved before fingerprints existed get one now (once).
{
    let added = false;
    for (const a of applicants) {
        if (a.cvSha256) continue;
        try { a.cvSha256 = fileHash(path.join(UPLOAD_DIR, a.cvFile)); added = true; } catch { /* upload gone */ }
    }
    if (added) saveApplicants();
}

// One CV at a time: keeps numbering safe and the machine responsive.
let queue = Promise.resolve();
export const inQueue = job => (queue = queue.then(job, job));

export const listApplicants = () => applicants;
export const applicantsForRole = roleId => applicants.filter(a => a.roleId === roleId);
export const findApplicant = candidateId => applicants.find(a => a.candidateId === candidateId) || null;

/**
 * Record one application: run Oscar on the CV and store everything.
 * `cvPath` / `linkedinPath` must already be inside UPLOAD_DIR.
 * Returns { duplicate: true } or { applicant, result } (result.status: ok | warning | failed).
 */
export async function submitApplication({ role, cvPath, linkedinPath = null, name, linkedinUrl = '', instagramUrl = '', practical }) {
    const sha = fileHash(cvPath);
    return inQueue(async () => {
        // checked inside the queue so two identical uploads at once can't both get in
        if (applicants.some(a => a.roleId === role.id && a.cvSha256 === sha)) return { duplicate: true };
        const number = applicants.reduce((m, a) => Math.max(m, a.number), 0) + 1;
        const r = await runCvReader(cvPath, number, role.rules);
        const applicant = {
            number, candidateId: r.candidateId, roleId: role.id, name, instagramUrl, linkedinUrl,
            appliedAt: new Date().toISOString(),
            cvStatus: r.status, cvCodes: r.codes,
            cvFile: path.basename(cvPath), cvSha256: sha, rulesKey: rulesKey(role.rules),
            linkedinFile: linkedinPath ? path.basename(linkedinPath) : null,
            practical,
        };
        applicants.push(applicant);
        saveApplicants();
        return { applicant, result: r };
    });
}

/**
 * Re-run Oscar on a role's CVs with the role's current keep/drop settings.
 * onlyStale: skip CVs already processed with exactly these settings (the full pipeline uses this).
 */
export async function reprocessRole(roleId, rules, onProgress, { onlyStale = false } = {}) {
    const key = rulesKey(rules);
    const list = applicantsForRole(roleId).filter(a => !onlyStale || a.rulesKey !== key);
    let done = 0, failed = 0;
    for (const a of list) {
        await inQueue(async () => {
            try {
                const r = await runCvReader(path.join(UPLOAD_DIR, a.cvFile), a.number, rules);
                Object.assign(a, { cvStatus: r.status, cvCodes: r.codes, rulesKey: key, processedAt: new Date().toISOString() });
                if (r.status === 'failed') failed++;
            } catch (e) {
                failed++;
                Object.assign(a, { cvStatus: 'failed', cvCodes: ['REPROCESS_ERROR'] });
                console.error(`reprocess ${a.candidateId}:`, e.message);
            }
            saveApplicants();
        });
        onProgress?.(++done, list.length);
    }
    return { total: list.length, failed };
}
