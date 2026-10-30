// What you can do with batches and the CVs in them: archive, restore, delete, and the weekly question.
// No express import: used by batchRoutes.js (and the tests).
//
//   archive   the CVs leave every run and every list; their files and Oscar's reads stay, so they can be
//             restored at any time (a CV sent again while archived is restored by submitApplication)
//   delete    permanent: the applicant record, the CV and LinkedIn files, the arrival read (data/runs/<id>)
//             and every role's own read of it. The candidate number is never given again (batches.js).
//             Finished reports that mention the person are left as they are.
//
// Archiving and deleting wait while any pipeline runs: a run reads the pool's files until it ends.
import fs from 'node:fs';
import path from 'node:path';
import { RUNS_DIR } from './cvRunner.js';
import { UPLOAD_DIR, inQueue, listApplicants, removeApplicants, saveApplicants } from './applicants.js';
import { forgetReads } from './reads.js';
import { listRoles, saveRoles } from './rolesStore.js';
import {
    dueAt, dueBatches, getBatch, keepBatch, listBatches, removeBatch, settings, snoozeBatch,
} from './batches.js';

const inBatch = id => listApplicants().filter(a => a.batch === id);
const activeIn = b => inBatch(b.id).filter(a => !a.archived).length;

/** Why the pool can't be changed right now, or null. */
export function poolBusy() {
    const running = listRoles().find(r => Object.values(r.agents || {}).some(a => a.state === 'running'));
    return running ? `A pipeline is running for "${running.title}". Archive or delete once it has finished.` : null;
}

/** Every batch with its numbers, newest first (the pipeline page's list). */
export function batchSummaries(now = new Date()) {
    const due = new Set(dueBatches(activeIn, now).map(b => b.id));
    const counts = {};
    for (const a of listApplicants()) {
        const c = counts[a.batch] ||= { total: 0, active: 0, archived: 0, first: null, last: null };
        c.total++;
        a.archived ? c.archived++ : c.active++;
        if (!c.first || a.appliedAt < c.first) c.first = a.appliedAt;
        if (!c.last || a.appliedAt > c.last) c.last = a.appliedAt;
    }
    return listBatches().map(b => {
        const c = counts[b.id] || { total: 0, active: 0, archived: 0, first: null, last: null };
        return { ...b, ...c, status: c.total && !c.active ? 'archived' : 'active', due: due.has(b.id),
            dueAt: dueAt(b)?.toISOString() || null,
            usedBy: listRoles().filter(r => r.scope === 'batches' && (r.batches || []).includes(b.id)).map(r => r.id) };
    }).sort((x, y) => String(y.createdAt).localeCompare(String(x.createdAt)));
}

/** Untick these batches in every role that runs on batches (a batch fully archived or deleted). */
function dropFromRoles(batchIds) {
    const gone = new Set(batchIds);
    let changed = false;
    for (const r of listRoles()) {
        const keep = (r.batches || []).filter(id => !gone.has(id));
        if (keep.length !== (r.batches || []).length) { r.batches = keep; changed = true; }
    }
    if (changed) saveRoles();
}
/** Batches that had CVs and now have no active one: they leave the roles' choices. */
const emptiedBatches = ids => [...new Set(ids)].filter(id => inBatch(id).length && !inBatch(id).some(a => !a.archived));

const pick = ids => {
    const want = new Set(ids);
    return listApplicants().filter(a => want.has(a.candidateId));
};

// ---------------- archive / restore ----------------
export function archiveCvs(candidateIds) {
    const busy = poolBusy();
    if (busy) return { error: busy };
    const list = pick(candidateIds).filter(a => !a.archived);
    for (const a of list) a.archived = true;
    if (list.length) saveApplicants();
    dropFromRoles(emptiedBatches(list.map(a => a.batch)));
    return { archived: list.length };
}
export function restoreCvs(candidateIds) {
    const list = pick(candidateIds).filter(a => a.archived);
    for (const a of list) delete a.archived;
    if (list.length) saveApplicants();
    return { restored: list.length };
}
export const archiveBatch = id => (getBatch(id) ? archiveCvs(inBatch(id).map(a => a.candidateId)) : { error: 'Unknown batch.' });
export const restoreBatch = id => (getBatch(id) ? restoreCvs(inBatch(id).map(a => a.candidateId)) : { error: 'Unknown batch.' });

// ---------------- delete (permanent) ----------------
/** Delete these CVs for good. Waits for the CV queue, so no CV is half-read while its files go. */
export async function deleteCvs(candidateIds) {
    const busy = poolBusy();
    if (busy) return { error: busy };
    return inQueue(async () => {
        const list = pick(candidateIds);
        if (!list.length) return { deleted: 0 };
        const ids = list.map(a => a.candidateId);
        for (const a of list) {
            for (const f of [a.cvFile, a.linkedinFile]) if (f) fs.rmSync(path.join(UPLOAD_DIR, path.basename(f)), { force: true });
            fs.rmSync(path.join(RUNS_DIR, a.candidateId), { recursive: true, force: true });
        }
        for (const r of listRoles()) forgetReads(r, ids);
        const batchIds = list.map(a => a.batch);
        removeApplicants(ids);
        dropFromRoles([...new Set(batchIds)].filter(id => !inBatch(id).some(a => !a.archived)));
        return { deleted: ids.length };
    });
}
/** Delete a whole batch: its CVs (archived ones too), then the batch itself. */
export async function deleteBatch(id) {
    if (!getBatch(id)) return { error: 'Unknown batch.' };
    const out = await deleteCvs(inBatch(id).map(a => a.candidateId));
    if (out.error) return out;
    removeBatch(id);
    dropFromRoles([id]);
    return out;
}

// ---------------- the weekly question ----------------
/** Weekly batches waiting for "keep, archive or delete?" (the banner in the lobby and the pipeline page). */
export const pendingReviews = (now = new Date()) => batchSummaries(now).filter(b => b.due);

export async function answerReview(id, action) {
    const b = getBatch(id);
    if (!b || b.kind !== 'weekly') return { error: 'Unknown weekly batch.' };
    if (action === 'keep') { keepBatch(id); return { kept: true }; }
    if (action === 'later') { snoozeBatch(id); return { later: true }; }
    if (action === 'archive') {
        const out = archiveBatch(id);
        if (!out.error) keepBatch(id);              // answered: an archived week is not asked about again
        return out;
    }
    if (action === 'delete') return deleteBatch(id);
    return { error: 'Answer keep, archive, delete or later.' };
}

export { settings };
