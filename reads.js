// Oscar's reads of the CV pool, per role.
//
// Every CV is read once when it arrives, with DEFAULT_RULES, into data/runs/<C0001>/ (the "arrival read").
// The CV sections a role keeps change three of Oscar's outputs (the cleaned text, the structured
// record and the "meaning" fingerprint), so a role whose sections differ from DEFAULT_RULES gets its
// OWN reads, in data/roles/<id>/runs/<C0001>/, listed in data/roles/<id>/reads.json:
//   { "C0001": { "key": "<rulesKey>", "status": "ok", "codes": [...], "at": "<iso>" }, ... }
// A role's read is only redone when the role's sections change (its key no longer matches).
// A role on the default sections simply uses the arrival reads.
//
// No express import: used by pipeline.js.
import fs from 'node:fs';
import path from 'node:path';
import { RUNS_DIR, runCvReader } from './cvRunner.js';
import { DEFAULT_KEY, UPLOAD_DIR, inQueue, refreshDefaultReads, rulesKey } from './applicants.js';
import { roleDir } from './rolesStore.js';

export const usesArrivalReads = role => rulesKey(role.rules) === DEFAULT_KEY;

/** Where the agents read this role's candidates from (`--runs`). */
export const runsDirFor = role => (usesArrivalReads(role) ? RUNS_DIR : path.join(roleDir(role.id), 'runs'));

const indexFile = role => path.join(roleDir(role.id), 'reads.json');
function loadIndex(role) {
    try { return JSON.parse(fs.readFileSync(indexFile(role), 'utf8')); } catch { return {}; }
}
function saveIndex(role, index) {
    const file = indexFile(role), tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(index));
    fs.renameSync(tmp, file);
}

/** How many of `list` still need a read before this role can run (shown before a run). */
export function staleCount(role, list) {
    if (usesArrivalReads(role)) return list.filter(a => a.rulesKey !== DEFAULT_KEY).length;
    const key = rulesKey(role.rules), index = loadIndex(role);
    return list.filter(a => index[a.candidateId]?.key !== key).length;
}

/**
 * Make sure every applicant in `list` has an up-to-date read for this role, reading only what is missing
 * or stale. Returns { readable: [candidateIds], total: reads done, failed }.
 */
export async function ensureReads(role, list, onProgress, signal) {
    if (usesArrivalReads(role)) {
        const r = await refreshDefaultReads(list, onProgress, signal);
        return { ...r, readable: list.filter(a => a.cvStatus !== 'failed').map(a => a.candidateId) };
    }
    const key = rulesKey(role.rules);
    const rules = structuredClone(role.rules);
    const root = runsDirFor(role);
    const index = loadIndex(role);
    const stale = list.filter(a => index[a.candidateId]?.key !== key);
    let done = 0, failed = 0;
    for (const a of stale) {
        if (signal?.aborted) break;                     // stopped: the CV being read finishes, the rest wait
        await inQueue(async () => {
            if (signal?.aborted) return;
            let entry;
            try {
                const r = await runCvReader(path.join(UPLOAD_DIR, a.cvFile), a.number, rules, root);
                entry = { key, status: r.status, codes: r.codes, at: new Date().toISOString() };
            } catch (e) {
                console.error(`read ${a.candidateId} for ${role.id}:`, e.message);
                entry = { key, status: 'failed', codes: ['REPROCESS_ERROR'], at: new Date().toISOString() };
            }
            if (entry.status === 'failed') failed++;
            index[a.candidateId] = entry;
            if (++done % 25 === 0 || done === stale.length) saveIndex(role, index);   // a stopped run keeps what it read
        });
        onProgress?.(done, stale.length);
    }
    if (stale.length) saveIndex(role, index);
    return { total: stale.length, failed,
        readable: list.filter(a => index[a.candidateId]?.status && index[a.candidateId].status !== 'failed').map(a => a.candidateId) };
}

/** Forget deleted CVs in this role's own reads: their folders and their lines in reads.json. */
export function forgetReads(role, candidateIds) {
    const index = loadIndex(role);
    let changed = false;
    for (const cid of candidateIds) {
        fs.rmSync(path.join(roleDir(role.id), 'runs', cid), { recursive: true, force: true });
        if (cid in index) { delete index[cid]; changed = true; }
    }
    if (changed) saveIndex(role, index);
}
