// node --test tests/   CV batches: Batch 0, weekly batches, uploads, archive / restore / delete, the weekly
// question, and roles that run on batches. Runs the real CV reader through Python (no AI), in a scratch folder.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'batches-test-'));
process.chdir(TMP);
process.env.PYTHONPATH = REPO;
process.env.CV_EMBED_BACKEND = 'hashing';

// a pool from before batches: two CVs, no batch, one role
fs.mkdirSync('data/uploads', { recursive: true });
fs.writeFileSync('data/roles.json', JSON.stringify([{ id: 'R001', title: 'Data engineer', status: 'open', agents: {},
    job: { title: 'Data engineer', summary: '', must_haves: [{ text: 'Python', type: 'skill', keywords: ['python'] }], nice_to_haves: [] },
    rules: { sections: { interests: 'drop', references: 'drop', declaration: 'drop' } }, logistics: {}, scope: 'pool', tieBreak: false }]));
const legacy = n => ({ number: n, candidateId: `C000${n}`, appliedFor: [], name: `Old ${n}`, appliedAt: '2026-09-01T10:00:00.000Z',
    cvFile: `old${n}.txt`, cvStatus: 'ok', cvCodes: [], rulesKey: '[]', practical: null, source: 'form' });
fs.writeFileSync('data/applicants.json', JSON.stringify([legacy(1), legacy(2)]));
fs.writeFileSync('data/uploads/old1.txt', 'old cv 1');
fs.writeFileSync('data/uploads/old2.txt', 'old cv 2');

const A = await import(path.join(REPO, 'applicants.js'));
const B = await import(path.join(REPO, 'batches.js'));
const O = await import(path.join(REPO, 'batchOps.js'));
const { getRole } = await import(path.join(REPO, 'rolesStore.js'));
const { pipelineProblem, applicationLabel } = await import(path.join(REPO, 'pipeline.js'));
const { RUNS_DIR } = await import(path.join(REPO, 'cvRunner.js'));

const CV = n => fs.readFileSync(path.join(REPO, 'demo/cvs', fs.readdirSync(path.join(REPO, 'demo/cvs')).sort()[n]), 'utf8');
const upload = (text, ext = '.txt') => {
    const f = path.join(A.UPLOAD_DIR, `${Date.now()}-${Math.random().toString(36).slice(2)}${ext}`);
    fs.writeFileSync(f, text);
    return f;
};
const ids = list => list.map(a => a.candidateId).sort();

test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));

test('every CV already in the pool goes into Batch 0, once', () => {
    const b0 = B.listBatches().find(b => b.kind === 'initial');
    assert.ok(b0);
    assert.deepEqual(A.listApplicants().map(a => a.batch), [b0.id, b0.id]);
    assert.equal(B.listBatches().filter(b => b.kind === 'initial').length, 1);
});

test('the form puts CVs into the batch of their week; an upload goes into the batch it was sent to', async () => {
    const monday = await A.submitApplication({ cvPath: upload(CV(0)), name: 'Amélie', practical: null, appliedAt: '2026-11-02T09:00:00.000Z' });
    const sunday = await A.submitApplication({ cvPath: upload(CV(1)), name: 'Rajesh', practical: null, appliedAt: '2026-11-08T18:00:00.000Z' });
    const next = await A.submitApplication({ cvPath: upload(CV(2)), name: 'Sophie', practical: null, appliedAt: '2026-11-09T09:00:00.000Z' });
    assert.equal(monday.applicant.batch, sunday.applicant.batch);
    assert.notEqual(next.applicant.batch, monday.applicant.batch);
    const week = B.getBatch(monday.applicant.batch);
    assert.equal(week.kind, 'weekly');
    assert.equal(week.weekStart, '2026-11-02');
    assert.equal(week.name, 'Week of 2 Nov 2026');

    const client = B.createBatch('Client X').batch;
    assert.ok(B.createBatch('client x').error, 'names are unique');
    const up = await A.submitApplication({ cvPath: upload(CV(3)), name: 'From file', nameFromCv: true, practical: null,
        source: 'batch', batchId: client.id });
    assert.equal(up.applicant.batch, client.id);
    // no form: Nina is told it was uploaded in a batch, not that it is an open application
    assert.deepEqual(A.applicationFor(up.applicant, 'R001'), { kind: 'batch', batch: 'Client X' });
    assert.deepEqual(applicationLabel(up.applicant, getRole('R001')), { kind: 'batch', batch: 'Client X' });

    // the same CV uploaded into another batch: it moves there (one CV, one batch)
    const other = B.createBatch('Client Y').batch;
    const moved = await A.submitApplication({ cvPath: upload(CV(3)), name: 'x', practical: null, source: 'batch', batchId: other.id });
    assert.equal(moved.moved, true);
    assert.equal(moved.from, 'Client X');
    assert.equal(A.findApplicant(up.applicant.candidateId).batch, other.id);
});

test('a role can run on chosen batches only; archived CVs never count', async () => {
    const role = getRole('R001');
    const client = B.listBatches().find(b => b.name === 'Client Y');
    role.scope = 'batches'; role.batches = [];
    assert.match(pipelineProblem(role), /Choose at least one batch/);
    role.batches = [client.id];
    assert.equal(pipelineProblem(role), null);
    assert.equal(A.applicantsInScope(role).length, 1);

    const out = O.archiveBatch(client.id);
    assert.equal(out.archived, 1);
    assert.equal(A.applicantsInScope({ scope: 'pool' }).some(a => a.batch === client.id), false);
    assert.deepEqual(role.batches, [], 'a fully archived batch leaves the roles that ran on it');
    assert.equal(O.batchSummaries().find(b => b.id === client.id).status, 'archived');

    assert.equal(O.restoreBatch(client.id).restored, 1);
    assert.equal(A.activeApplicants().filter(a => a.batch === client.id).length, 1);
    role.scope = 'pool';
});

test('an archived CV sent again on the form comes back, into this week', async () => {
    const amelie = A.listApplicants().find(a => a.name === 'Amélie');
    O.archiveCvs([amelie.candidateId]);
    assert.equal(A.findApplicant(amelie.candidateId).archived, true);
    const again = await A.submitApplication({ cvPath: upload(CV(0)), name: 'Amélie', practical: null });
    assert.equal(again.restored, true);
    assert.equal(again.applicant.archived, undefined);
    assert.equal(again.applicant.batch, B.weeklyBatch(new Date()).id);
    assert.equal(A.listApplicants().filter(a => a.name === 'Amélie').length, 1);
});

test('delete is permanent: record, files and reads go, and the number is never given again', async () => {
    const role = getRole('R001');
    const sophie = A.listApplicants().find(a => a.name === 'Sophie');
    const cid = sophie.candidateId;
    // pretend R001 keeps other sections and has its own read of Sophie
    const own = path.join(TMP, 'data/roles/R001/runs', cid);
    fs.mkdirSync(own, { recursive: true });
    fs.writeFileSync(path.join(TMP, 'data/roles/R001/reads.json'), JSON.stringify({ [cid]: { key: 'x', status: 'ok' } }));
    assert.ok(fs.existsSync(path.join(A.UPLOAD_DIR, sophie.cvFile)));
    assert.ok(fs.existsSync(path.join(RUNS_DIR, cid)));
    const highest = Math.max(...A.listApplicants().map(a => a.number));

    const out = await O.deleteCvs([cid]);
    assert.equal(out.deleted, 1);
    assert.equal(A.findApplicant(cid), null);
    assert.equal(fs.existsSync(path.join(A.UPLOAD_DIR, sophie.cvFile)), false);
    assert.equal(fs.existsSync(path.join(RUNS_DIR, cid)), false);
    assert.equal(fs.existsSync(own), false);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(TMP, 'data/roles/R001/reads.json'), 'utf8')), {});
    void role;

    // delete the CV with the highest number too: the next CV still gets a new number
    const top = A.listApplicants().find(a => a.number === highest);
    if (top) await O.deleteCvs([top.candidateId]);
    const next = await A.submitApplication({ cvPath: upload(CV(4)), name: 'Next', practical: null });
    assert.equal(next.applicant.number, highest + 1);
});

test('deleting a whole batch removes its CVs (archived ones too), the batch, and its place in roles', async () => {
    const role = getRole('R001');
    const b0 = B.listBatches().find(b => b.kind === 'initial');
    O.archiveCvs(['C0001']);
    role.scope = 'batches'; role.batches = [b0.id];
    const out = await O.deleteBatch(b0.id);
    assert.equal(out.deleted, 2);
    assert.equal(B.getBatch(b0.id), null);
    assert.deepEqual(role.batches, []);
    assert.equal(A.listApplicants().some(a => ['C0001', 'C0002'].includes(a.candidateId)), false);
    assert.equal(fs.existsSync(path.join(A.UPLOAD_DIR, 'old1.txt')), false);
    role.scope = 'pool';
});

test('nothing is archived or deleted while a pipeline runs', async () => {
    const role = getRole('R001');
    role.agents = { pipeline: { state: 'running' } };
    const someone = A.activeApplicants()[0].candidateId;
    assert.match(O.archiveCvs([someone]).error, /pipeline is running/);
    assert.match((await O.deleteCvs([someone])).error, /pipeline is running/);
    assert.ok(A.findApplicant(someone));
    role.agents = {};
});

test('the weekly question: due N days after the week ends, never for uploads; keep / later / archive', async () => {
    const week = B.getBatch(A.listApplicants().find(a => a.name === 'Rajesh').batch);   // week of 2 Nov 2026
    const before = new Date('2026-11-15T12:00:00');         // week ended on the 8th: 7 days later is the 16th
    const after = new Date('2026-11-16T12:00:00');
    assert.equal(O.pendingReviews(before).some(b => b.id === week.id), false);
    assert.equal(O.pendingReviews(after).some(b => b.id === week.id), true);
    assert.equal(O.pendingReviews(new Date('2027-06-01')).some(b => b.kind !== 'weekly'), false);

    B.setReviewAfterDays(14);
    assert.equal(O.pendingReviews(after).some(b => b.id === week.id), false);
    assert.equal(O.pendingReviews(new Date('2026-11-23T12:00:00')).some(b => b.id === week.id), true);
    assert.ok(B.setReviewAfterDays(10).error);
    B.setReviewAfterDays(7);

    B.snoozeBatch(week.id, after);                          // "ask me later": a week later
    assert.equal(O.pendingReviews(new Date('2026-11-20T12:00:00')).some(b => b.id === week.id), false);
    assert.equal(O.pendingReviews(new Date('2026-11-24T12:00:00')).some(b => b.id === week.id), true);

    const kept = await O.answerReview(week.id, 'keep');
    assert.equal(kept.kept, true);
    assert.equal(O.pendingReviews(new Date('2027-06-01')).some(b => b.id === week.id), false);
    assert.equal(A.activeApplicants().some(a => a.batch === week.id), true, 'keeping changes nothing else');
});
