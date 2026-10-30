// node --test tests/   The lobby's "Stop & reset": a running step is killed, the run's results and the timeline's
// steps are cleared, the job and the CVs are kept, and the role can run again. No AI: Python only sleeps.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'reset-test-'));
process.chdir(TMP);
process.env.PYTHONPATH = REPO;

const job = { title: 'Data engineer', summary: '', must_haves: [{ text: 'Python', type: 'skill', keywords: ['python'] }], nice_to_haves: [] };
fs.mkdirSync('data/uploads', { recursive: true });
fs.writeFileSync('data/applicants.json', '[]');
fs.writeFileSync('data/roles.json', JSON.stringify([{ id: 'R001', title: 'Data engineer', status: 'open', job,
    agents: { clara: { state: 'done', message: 'Criteria proposed.' } },
    rules: { sections: { interests: 'drop', references: 'drop', declaration: 'drop' } }, logistics: {}, scope: 'pool', tieBreak: false }]));

const { getRole, roleDir } = await import(path.join(REPO, 'rolesStore.js'));
const { OUTPUTS, STEPS, startAgent, stopAndReset } = await import(path.join(REPO, 'pipeline.js'));
const { runPython } = await import(path.join(REPO, 'pyRunner.js'));

const SLEEP = ['-c', 'import time; time.sleep(30)'];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const outputs = id => OUTPUTS.filter(f => fs.existsSync(path.join(roleDir(id), f)));
function fakeResults(role) {
    fs.mkdirSync(roleDir(role.id), { recursive: true });
    for (const f of OUTPUTS) fs.writeFileSync(path.join(roleDir(role.id), f), '{}');
    fs.writeFileSync(path.join(roleDir(role.id), 'progress.json'), '{}');
    fs.writeFileSync(path.join(roleDir(role.id), 'job.json'), JSON.stringify(job));
}

test('an aborted Python run is killed at once', async () => {
    const c = new AbortController();
    const started = Date.now();
    const run = runPython(SLEEP, 60000, c.signal);
    await sleep(300);
    c.abort(new Error('stop'));
    await assert.rejects(run, /stop/);
    assert.ok(Date.now() - started < 5000, 'the 30 s sleep was not killed');
});

test('Stop & reset kills the running step and clears the run', async () => {
    const role = getRole('R001');
    fakeResults(role);
    let signalSeen = null;
    const busy = startAgent(role, 'pipeline', async (progressFile, signal) => {
        signalSeen = signal;
        role.agents.remi = { state: 'running', startedAt: new Date().toISOString() };
        await runPython(SLEEP, 60000, signal);
    });
    assert.equal(busy, null);
    await sleep(300);
    assert.equal(startAgent(role, 'pipeline', async () => {}), 'Something is already running for this role. Wait for it to finish.');

    const started = Date.now();
    const stopped = await stopAndReset(role);
    assert.equal(stopped, 'pipeline');
    assert.ok(signalSeen.aborted);
    assert.ok(Date.now() - started < 5000, 'stopping took too long');

    // the run's results and the timeline's steps are gone ...
    assert.deepEqual(outputs('R001'), []);
    assert.equal(fs.existsSync(path.join(roleDir('R001'), 'progress.json')), false);
    for (const s of ['pipeline', ...STEPS]) assert.equal(role.agents[s], undefined, s);
    // ... the job, Clara's criteria and the job file stay
    assert.deepEqual(role.job, job);
    assert.equal(role.agents.clara.state, 'done');
    assert.ok(fs.existsSync(path.join(roleDir('R001'), 'job.json')));
    // and it is saved: a restart sees the reset role
    const saved = JSON.parse(fs.readFileSync('data/roles.json', 'utf8')).find(r => r.id === 'R001');
    assert.equal(saved.agents.pipeline, undefined);

    // the role can run again right away
    assert.equal(startAgent(role, 'pipeline', async () => 'ok'), null);
    await sleep(50);
    assert.equal(role.agents.pipeline.state, 'done');
});

test('Reset with nothing running clears a finished or failed run', async () => {
    const role = getRole('R001');
    fakeResults(role);
    role.agents.pipeline = { state: 'failed', message: 'Committee + Gaston failed: LLM ERROR' };
    role.agents.jules = { state: 'done' };
    role.agents.committee = { state: 'failed' };
    assert.equal(await stopAndReset(role), null);
    assert.deepEqual(outputs('R001'), []);
    for (const s of ['pipeline', ...STEPS]) assert.equal(role.agents[s], undefined, s);
    assert.equal(role.agents.clara.state, 'done');
});

test('A step left "running" with nothing behind it (crash, terminal run) is cleared too', async () => {
    const role = getRole('R001');
    role.agents.clara = { state: 'running' };
    role.agents.oscar = { state: 'running' };
    assert.equal(await stopAndReset(role), null);
    assert.equal(role.agents.clara, undefined);
    assert.equal(role.agents.oscar, undefined);
    assert.equal(startAgent(role, 'pipeline', async () => 'ok'), null);
    await sleep(50);
});
