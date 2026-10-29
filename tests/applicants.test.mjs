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

const { UPLOAD_DIR, submitApplication, listApplicants } = await import(path.join(REPO, 'applicants.js'));
const { createRole } = await import(path.join(REPO, 'rolesStore.js'));

const upload = (name, text) => {
    const f = path.join(UPLOAD_DIR, `${Date.now()}-${Math.random().toString(36).slice(2)}-${name}`);
    fs.writeFileSync(f, text);
    return f;
};
const GOOD_CV = fs.readFileSync(path.join(REPO, 'demo/cvs/01_amelie_laurent.txt'), 'utf8');

test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));

test('a CV that cannot be read is not kept, so the applicant can send it again', async () => {
    const role = createRole('Data engineer');
    const first = await submitApplication({ role, cvPath: upload('bad.txt', 'x'), name: 'Bad File', practical: null });
    assert.equal(first.result.status, 'failed');
    assert.equal(first.applicant, null);
    assert.equal(listApplicants().filter(a => a.name === 'Bad File').length, 0);

    // the same unreadable file again: told it failed again, never "we already have this CV"
    const again = await submitApplication({ role, cvPath: upload('bad.txt', 'x'), name: 'Bad File', practical: null });
    assert.notEqual(again.duplicate, true);
    assert.equal(again.result.status, 'failed');
});

test('a readable CV is kept, and the same file twice for one role is refused', async () => {
    const role = createRole('Data engineer');
    const ok = await submitApplication({ role, cvPath: upload('cv.txt', GOOD_CV), name: 'Amélie', practical: null });
    assert.equal(ok.result.status, 'ok');
    assert.ok(ok.applicant.candidateId);
    const dup = await submitApplication({ role, cvPath: upload('cv.txt', GOOD_CV), name: 'Amélie', practical: null });
    assert.equal(dup.duplicate, true);
});

test('the original CV file name is kept, made safe, with the stored file\'s extension', async () => {
    const { cleanFileName } = await import(path.join(REPO, 'applicants.js'));
    assert.equal(cleanFileName('Amélie Laurent CV.pdf', 'x/abc.pdf'), 'Amélie Laurent CV.pdf');
    assert.equal(cleanFileName('C:\\Users\\me\\cv.pdf', 'abc.pdf'), 'cv.pdf');
    assert.equal(cleanFileName('../../etc/passwd', 'abc.txt'), 'passwd.txt');
    assert.equal(cleanFileName('bad"name<>.docx', 'abc.docx'), 'badname.docx');
    assert.equal(cleanFileName('', 'abc.pdf'), 'CV.pdf');
    assert.equal(cleanFileName('.hidden', 'abc.txt'), 'CV.txt');

    const role = createRole('Data engineer');
    const ok = await submitApplication({ role, cvPath: upload('cv.txt', GOOD_CV + '\n'), cvName: 'Amélie CV.txt', name: 'Amélie N', practical: null });
    assert.equal(ok.applicant.cvName, 'Amélie CV.txt');
});
