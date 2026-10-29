// node --test tests/   (no dependencies; runs the real CV reader through Python, no AI)
// Runs in a scratch folder so it never touches the real data/ directory.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'applicants-test-'));
process.chdir(TMP);                                       // data/ is created here
process.env.PYTHONPATH = REPO;                            // so `python -m cv_reader` is found from the scratch folder
process.env.CV_EMBED_BACKEND = 'hashing';

// data saved before the CV pool: one role, an applicant tied to it with the old right-to-work answer
fs.mkdirSync('data', { recursive: true });
fs.writeFileSync('data/roles.json', JSON.stringify([{ id: 'R001', title: 'Old role', status: 'open', agents: {},
    job: { title: 'Old role', summary: '', must_haves: [], nice_to_haves: [] }, rules: { sections: {} },
    logistics: { currency: 'EUR', work_country: 'Mauritius', languages: [] } }]));
fs.writeFileSync('data/applicants.json', JSON.stringify([{ number: 1, candidateId: 'C0001', roleId: 'R001', name: 'Legacy',
    appliedAt: '2026-10-01T10:00:00.000Z', cvFile: 'gone.txt', cvStatus: 'ok', cvCodes: [], rulesKey: '[]',
    practical: { salary: { amount: 4000, period: 'month', currency: 'EUR' }, work_authorization: 'yes', languages: [] } }]));

const A = await import(path.join(REPO, 'applicants.js'));
const { UPLOAD_DIR, submitApplication, listApplicants, applicantsInScope, applicationFor, DEFAULT_KEY } = A;
const { createRole, getRole } = await import(path.join(REPO, 'rolesStore.js'));
const reads = await import(path.join(REPO, 'reads.js'));
const { RUNS_DIR } = await import(path.join(REPO, 'cvRunner.js'));

const upload = (name, text) => {
    const f = path.join(UPLOAD_DIR, `${Date.now()}-${Math.random().toString(36).slice(2)}-${name}`);
    fs.writeFileSync(f, text);
    return f;
};
const GOOD_CV = fs.readFileSync(path.join(REPO, 'demo/cvs/01_amelie_laurent.txt'), 'utf8');
const OTHER_CV = fs.readFileSync(path.join(REPO, 'demo/cvs/02_rajesh_kumar.txt'), 'utf8');

test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));

test('records from before the pool are upgraded once, on start', () => {
    const old = listApplicants().find(a => a.candidateId === 'C0001');
    assert.deepEqual(old.appliedFor, ['R001']);
    assert.equal('roleId' in old, false);
    assert.deepEqual(old.practical.work_countries, ['Mauritius']);       // "yes" for a role working in Mauritius
    assert.equal(getRole('R001').scope, 'pool');
    assert.equal(getRole('R001').tieBreak, false);
    assert.notEqual(old.rulesKey, DEFAULT_KEY);                         // read with other sections: re-read when a role runs
});

test('a CV that cannot be read is not kept, so the applicant can send it again', async () => {
    const first = await submitApplication({ cvPath: upload('bad.txt', 'x'), name: 'Bad File', practical: null });
    assert.equal(first.result.status, 'failed');
    assert.equal(first.applicant, null);
    assert.equal(listApplicants().filter(a => a.name === 'Bad File').length, 0);

    // the same unreadable file again: told it failed again, never "we already have this CV"
    const again = await submitApplication({ cvPath: upload('bad.txt', 'x'), name: 'Bad File', practical: null });
    assert.notEqual(again.duplicate, true);
    assert.equal(again.result.status, 'failed');
});

test('the pool: one CV once; sent again for another role it is only marked as applying for it', async () => {
    const r1 = createRole('Data engineer'), r2 = createRole('Backend developer');
    const ok = await submitApplication({ roleId: r1.id, cvPath: upload('cv.txt', GOOD_CV), name: 'Amélie', practical: null });
    assert.equal(ok.result.status, 'ok');
    assert.deepEqual(ok.applicant.appliedFor, [r1.id]);
    assert.equal(ok.applicant.rulesKey, DEFAULT_KEY);                     // read with the pool's default sections

    const dup = await submitApplication({ roleId: r1.id, cvPath: upload('cv.txt', GOOD_CV), name: 'Amélie', practical: null });
    assert.equal(dup.duplicate, true);
    const openAgain = await submitApplication({ cvPath: upload('cv.txt', GOOD_CV), name: 'Amélie', practical: null });
    assert.equal(openAgain.duplicate, true);
    const added = await submitApplication({ roleId: r2.id, cvPath: upload('cv.txt', GOOD_CV), name: 'Amélie', practical: null });
    assert.equal(added.added, true);
    assert.deepEqual(added.applicant.appliedFor, [r1.id, r2.id]);
    assert.equal(listApplicants().filter(a => a.name === 'Amélie').length, 1);

    const open = await submitApplication({ cvPath: upload('cv2.txt', OTHER_CV), name: 'Rajesh', practical: null });
    assert.deepEqual(open.applicant.appliedFor, []);

    // what each role runs on, and how each applicant stands towards it
    assert.equal(applicantsInScope(r1).length, listApplicants().length);   // whole pool by default
    assert.deepEqual(applicantsInScope({ ...r1, scope: 'direct' }).map(a => a.name), ['Amélie']);
    assert.deepEqual(applicationFor(ok.applicant, r1.id), { kind: 'this' });
    assert.deepEqual(applicationFor(open.applicant, r1.id), { kind: 'open' });
    const r3 = createRole('Accountant');
    assert.deepEqual(applicationFor(ok.applicant, r3.id), { kind: 'other', roles: [r1.id, r2.id] });
});

test('the CV name Oscar finds can replace the given one (the import)', async () => {
    const r = await submitApplication({ cvPath: upload('cv3.txt', GOOD_CV + '\n\n'), name: 'Fallback', nameFromCv: true, practical: null });
    assert.equal(r.applicant.name, 'Amélie Laurent');
    assert.equal(r.applicant.source, 'form');
});

test('a role on the default sections uses the arrival reads; other sections get the role its own reads', async () => {
    const list = listApplicants().filter(a => a.cvFile !== 'gone.txt');
    const plain = createRole('Plain');
    assert.equal(reads.usesArrivalReads(plain), true);
    assert.equal(reads.runsDirFor(plain), RUNS_DIR);
    assert.equal(reads.staleCount(plain, list), 0);
    const r0 = await reads.ensureReads(plain, list);
    assert.equal(r0.total, 0);                                           // nothing to read
    assert.equal(r0.readable.length, list.length);

    const own = createRole('Keeps interests');
    own.rules = { sections: { references: 'drop', declaration: 'drop' } };   // interests kept
    assert.equal(reads.usesArrivalReads(own), false);
    assert.equal(reads.staleCount(own, list), list.length);
    const r1 = await reads.ensureReads(own, list);
    assert.equal(r1.total, list.length);
    assert.equal(r1.readable.length, list.length);
    const cid = list[0].candidateId;
    assert.ok(fs.existsSync(path.join(reads.runsDirFor(own), cid, 'cv_json', `${cid}.json`)));
    assert.equal(reads.staleCount(own, list), 0);
    assert.equal((await reads.ensureReads(own, list)).total, 0);          // read once, then reused

    own.rules = { sections: { interests: 'drop' } };                     // settings changed: read again
    assert.equal(reads.staleCount(own, list), list.length);
    // the arrival reads were never touched
    assert.equal(listApplicants().find(a => a.candidateId === cid).rulesKey, DEFAULT_KEY);
});

test('the original CV file name is kept, made safe, with the stored file\'s extension', async () => {
    const { cleanFileName } = A;
    assert.equal(cleanFileName('Amélie Laurent CV.pdf', 'x/abc.pdf'), 'Amélie Laurent CV.pdf');
    assert.equal(cleanFileName('C:\\Users\\me\\cv.pdf', 'abc.pdf'), 'cv.pdf');
    assert.equal(cleanFileName('../../etc/passwd', 'abc.txt'), 'passwd.txt');
    assert.equal(cleanFileName('bad"name<>.docx', 'abc.docx'), 'badname.docx');
    assert.equal(cleanFileName('', 'abc.pdf'), 'CV.pdf');
    assert.equal(cleanFileName('.hidden', 'abc.txt'), 'CV.txt');

    const ok = await submitApplication({ cvPath: upload('cv.txt', GOOD_CV + '\n'), cvName: 'Amélie CV.txt', name: 'Amélie N', practical: null });
    assert.equal(ok.applicant.cvName, 'Amélie CV.txt');
});
