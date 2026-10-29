// node --test tests/   The import command on a small folder (real CV reader, no AI), in a scratch folder.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'import-test-'));
const CVS = path.join(TMP, 'cvs');
fs.mkdirSync(path.join(CVS, 'sub'), { recursive: true });
const demo = f => fs.readFileSync(path.join(REPO, 'demo/cvs', f));
fs.writeFileSync(path.join(CVS, 'a.txt'), demo('01_amelie_laurent.txt'));
fs.writeFileSync(path.join(CVS, 'b.txt'), demo('02_rajesh_kumar.txt'));
fs.writeFileSync(path.join(CVS, 'sub', 'c.txt'), demo('03_sophie_martin.txt'));
fs.writeFileSync(path.join(CVS, 'sub', 'copy-of-a.txt'), demo('01_amelie_laurent.txt'));   // same file twice
fs.writeFileSync(path.join(CVS, 'unreadable.txt'), 'x');
fs.writeFileSync(path.join(CVS, 'photo.png'), 'not a cv');

const run = (...args) => execFileSync(process.execPath, [path.join(REPO, 'scripts/import-cvs.js'), ...args], {
    cwd: TMP, encoding: 'utf8', env: { ...process.env, PYTHONPATH: REPO, CV_EMBED_BACKEND: 'hashing', PORT: '39999' },
});
const pool = () => JSON.parse(fs.readFileSync(path.join(TMP, 'data/applicants.json'), 'utf8'));

test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));

test('imports every readable CV once, with a name and valid invented answers, and lists the rest', async () => {
    const out = run(CVS, '--open', '1');
    assert.match(out, /5 CV file\(s\) found, 1 other file\(s\) skipped/);
    assert.match(out, /3 added, 1 already in the pool, 1 unreadable/);
    const apps = pool();
    assert.equal(apps.length, 3);
    assert.ok(apps.some(a => a.name === 'Amélie Laurent'), apps.map(a => a.name).join(', '));
    for (const a of apps) {
        assert.equal(a.source, 'import');
        assert.deepEqual(a.appliedFor, []);                                // --open 1: all open applications
        assert.ok(a.name);
        const p = a.practical;
        assert.ok(p.salary.amount > 0 && /^[A-Z]{3}$/.test(p.salary.currency));
        assert.ok(p.work_countries.length >= 1 && p.languages.length >= 1);
        assert.match(p.available_from, /^\d{4}-\d{2}-\d{2}$/);
    }
    assert.equal(fs.readdirSync(path.join(TMP, 'data/uploads')).length, 3);   // no copies left for skipped files
    const log = fs.readdirSync(path.join(TMP, 'data')).find(f => f.startsWith('import-'));
    const text = fs.readFileSync(path.join(TMP, 'data', log), 'utf8');
    assert.match(text, /unreadable\.txt/);
    assert.match(text, /photo\.png/);
});

test('running it again adds nothing and changes nothing', async () => {
    const before = pool();
    const out = run(CVS);
    assert.match(out, /0 added, 4 already in the pool/);
    assert.deepEqual(pool().map(a => a.practical), before.map(a => a.practical));
});
