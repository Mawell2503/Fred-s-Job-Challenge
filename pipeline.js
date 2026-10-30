// The full pipeline for one role: Oscar -> Remi -> Jules -> Committee (+ Gaston), in the background.
// Clara stays manual on purpose: a person must approve the criteria first.
//
//   runPipeline(role, { llmGate }, progressFile)  -> summary message (throws with the failing step)
//
// No express import: used by roles.js (the "Run full pipeline" button) and by
// scripts/run-pipeline.js (the same thing from the terminal).
//
// Data flow worth knowing:
//   - A run reads the whole CV pool, only the role's direct applicants, or only the CVs in the batches
//     ticked in role.batches (role.scope). Archived CVs are never read.
//   - Oscar only reads what is missing or stale for this role (reads.js): nothing at all for a role on
//     the default CV sections whose CVs were read on arrival.
//   - The practical answers (Nina) and LinkedIn PDFs (Helene) never enter Oscar / Remi / Jules:
//     after Jules has picked the top 10, only THOSE 10 candidates' answers are written into
//     committee_in.json and handed to the committee.
import fs from 'node:fs';
import path from 'node:path';
import { runPython } from './pyRunner.js';
import { applicantsInScope, applicationFor } from './applicants.js';
import { ensureReads, runsDirFor } from './reads.js';
import { getRole, roleDir, saveRoles } from './rolesStore.js';

const MINUTES = 60_000;
export const TIMEOUTS = { clara: 5 * MINUTES, remi: 30 * MINUTES, jules: 60 * MINUTES, committee: 90 * MINUTES };
export const STEPS = ['oscar', 'remi', 'jules', 'committee'];
export const STEP_LABEL = { oscar: 'Oscar (CVs)', remi: 'Remi (top 20)', jules: 'Jules (top 10)', committee: 'Committee + Gaston' };
// everything a pipeline run produces; deleted at the start of a run so nothing stale is ever shown
export const OUTPUTS = ['ids.txt', 'remi.json', 'jules.json', 'committee_in.json', 'committee.json', 'report.md', 'trace.jsonl', 'feed.json', 'live.json'];

export const readJson = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; } };
const lastLine = text => String(text).trim().split('\n').filter(Boolean).pop() || '';

/** Start `work` for one agent of one role unless something is already running for that role. */
// the work running in this server, per role: what "Stop & reset" stops
const active = new Map();          // roleId -> { agent, controller, done }
const STOPPED = 'Stopped by an admin.';

export function startAgent(role, agent, work) {
    role.agents ||= {};
    if (active.has(role.id) || Object.values(role.agents).some(a => a.state === 'running')) {
        return 'Something is already running for this role. Wait for it to finish.';
    }
    role.agents[agent] = { state: 'running', startedAt: new Date().toISOString(), message: '' };
    saveRoles();
    const progressFile = path.join(roleDir(role.id), 'progress.json');
    fs.rmSync(progressFile, { force: true });
    const controller = new AbortController();
    const done = Promise.resolve()
        .then(() => work(progressFile, controller.signal))
        .then(message => Object.assign(role.agents[agent], { state: 'done', message: message || '' }))
        .catch(err => Object.assign(role.agents[agent], controller.signal.aborted
            ? { state: 'stopped', message: STOPPED }
            : { state: 'failed', message: String(err.message || err).slice(0, 800) }))
        .finally(() => {
            role.agents[agent].finishedAt = new Date().toISOString();
            active.delete(role.id);
            saveRoles();
        });
    active.set(role.id, { agent, controller, done });
    return null;
}

/**
 * The lobby's "Stop & reset": stop whatever runs for this role in this server, then clear the run's results
 * and the timeline's steps, so the role is back to "Run full pipeline". The job, its criteria, the CVs and
 * Oscar's reads of them are kept (they are not the sorting; reading the pool again would only cost time).
 * Returns what was stopped (an agent name) or null.
 */
export async function stopAndReset(role, { waitMs = 30000 } = {}) {
    const run = active.get(role.id);
    if (run) {
        run.controller.abort(new Error(STOPPED));
        let timer;
        const waited = await Promise.race([run.done.then(() => true),
            new Promise(r => { timer = setTimeout(() => r(false), waitMs); })]);
        clearTimeout(timer);
        if (!waited) throw new Error('The run did not stop in time. Try again in a moment.');
    }
    const dir = roleDir(role.id);
    for (const f of [...OUTPUTS, 'progress.json']) fs.rmSync(path.join(dir, f), { force: true });
    role.agents ||= {};
    for (const k of ['pipeline', ...STEPS]) delete role.agents[k];
    // a step marked running with nothing behind it (a run from the terminal, or a crash): cleared too
    for (const [k, a] of Object.entries(role.agents)) if (a.state === 'running' || a.state === 'stopped') delete role.agents[k];
    saveRoles();
    return run?.agent || null;
}

export async function runHiring(args, agent, signal) {
    const { code, stdout, stderr } = await runPython(['-m', 'hiring', ...args], TIMEOUTS[agent], signal);
    if (code !== 0) throw new Error(lastLine(stderr) || `${agent} failed (exit ${code}).`);
    return lastLine(stdout);
}

export function writeJob(role) {
    const file = path.join(roleDir(role.id), 'job.json');
    fs.writeFileSync(file, JSON.stringify(role.job, null, 2));
    return file;
}

/**
 * What the applicant chose, towards `role`, with role titles (for the committee, the report and the badges):
 *   { kind: 'this' } | { kind: 'open' } | { kind: 'other', roles: ['Backend Developer'] }
 *   | { kind: 'batch', batch: 'Client X · 31 Oct' }     uploaded into a batch by an admin: no application form
 */
export function applicationLabel(a, role) {
    const l = applicationFor(a, role.id);
    if (l.kind === 'other') l.roles = l.roles.map(id => getRole(id)?.title || id);
    return l;
}

/** Why the pipeline can't start yet, or null. */
export function pipelineProblem(role) {
    if (!role.job?.must_haves?.length && !role.job?.nice_to_haves?.length) {
        return 'Save the criteria first (ask Clara, check them, then "Save criteria").';
    }
    if (role.scope === 'batches' && !(role.batches || []).length) {
        return 'Choose at least one batch to run on (or run it on the whole CV pool).';
    }
    if (!applicantsInScope(role).length) {
        return role.scope === 'direct' ? 'Nobody applied for this role yet (or run it on the whole CV pool).'
            : role.scope === 'batches' ? 'The batches you chose have no active CVs.'
                : 'The CV pool is empty: no applications yet.';
    }
    return null;
}

export async function runPipeline(role, { llmGate = false } = {}, progressFile, signal) {
    const dir = roleDir(role.id);
    const file = name => path.join(dir, name);
    progressFile ||= file('progress.json');
    const progress = data => fs.writeFileSync(progressFile, JSON.stringify(data));
    for (const f of OUTPUTS) fs.rmSync(file(f), { force: true });
    role.agents ||= {};
    for (const s of STEPS) delete role.agents[s];
    saveRoles();

    const step = async (name, fn) => {
        signal?.throwIfAborted();
        role.agents[name] = { state: 'running', startedAt: new Date().toISOString(), message: '' };
        saveRoles();
        progress({ stage: name, done: 0, total: 0 });
        try {
            const message = await fn();
            Object.assign(role.agents[name], { state: 'done', message: message || '', finishedAt: new Date().toISOString() });
            saveRoles();
            return message;
        } catch (err) {
            Object.assign(role.agents[name], signal?.aborted ? { state: 'stopped', message: STOPPED, finishedAt: new Date().toISOString() }
                : { state: 'failed', message: String(err.message || err).slice(0, 800), finishedAt: new Date().toISOString() });
            saveRoles();
            if (signal?.aborted) throw signal.reason;
            throw new Error(`${STEP_LABEL[name]} failed: ${err.message || err}`);
        }
    };

    // who this run reads, fixed now: CVs arriving during the run wait for the next one
    const apps = [...applicantsInScope(role)];
    const runs = runsDirFor(role);

    // 1. Oscar: only what is missing or stale for this role's CV sections
    let ids = [];
    await step('oscar', async () => {
        const r = await ensureReads(role, apps, (done, total) => progress({ stage: 'oscar', done, total }), signal);
        signal?.throwIfAborted();
        ids = r.readable;
        return r.total ? `Read ${r.total} CV(s) with this role's sections${r.failed ? `, ${r.failed} unreadable` : ''}.`
            : `Every CV was already read with this role's sections (${ids.length} readable).`;
    });
    if (!ids.length) throw new Error('No readable CVs for this role.');
    const job = writeJob(role);
    // the candidate list goes through a file: thousands of ids are too long for a Windows command line
    const idsFile = file('ids.txt');
    fs.writeFileSync(idsFile, ids.join('\n'));

    // 2. Remi: must-have gate + top 20
    await step('remi', () => {
        const args = ['remi', '--job', job, '--runs', runs, '--ids-file', idsFile, '--out', file('remi.json'),
            '--progress', progressFile];
        if (llmGate) args.push('--llm-gate');
        return runHiring(args, 'remi', signal);
    });
    const remi = readJson(file('remi.json'));
    if (!remi?.shortlist?.length) throw new Error('Remi shortlisted nobody: every candidate failed a must-have.');

    // 3. Jules: full read of the 20, keeps 10
    await step('jules', () => runHiring(['jules', '--job', job, '--runs', runs, '--remi', file('remi.json'),
        '--out', file('jules.json'), '--top', '10', '--progress', progressFile], 'jules', signal));
    const jules = readJson(file('jules.json'));
    const finalists = (jules?.finalists || []).map(f => f.candidate_id);
    if (!finalists.length) throw new Error('Jules produced no finalists.');

    // 4. Committee: ONLY now do the practical answers, LinkedIn flags and what they applied for come in
    const byId = Object.fromEntries(apps.map(a => [a.candidateId, a]));
    const pool = role.scope !== 'direct';
    const cin = {
        role: { id: role.id, title: role.title },
        logistics: role.logistics || {},
        practical: Object.fromEntries(finalists.filter(c => byId[c]?.practical).map(c => [c, byId[c].practical])),
        linkedin: Object.fromEntries(finalists.map(c => [c, Boolean(byId[c]?.linkedinFile)])),
        // the name the applicant gave (or the import found): for the report only, never sent to the AI
        names: Object.fromEntries(finalists.filter(c => byId[c]?.name).map(c => [c, byId[c].name])),
        // whole pool: whether each finalist applied for this role (Nina's check). Direct only: everyone did.
        application: pool ? Object.fromEntries(finalists.filter(c => byId[c]).map(c => [c, applicationLabel(byId[c], role)])) : {},
        tie_break_direct: pool && role.tieBreak === true,
        funnel: { applications: apps.length, readable: ids.length, passed_gate: remi.counts?.passed_gate,
            read_by_jules: jules.counts?.read, scope: role.scope || 'pool' },
    };
    fs.writeFileSync(file('committee_in.json'), JSON.stringify(cin, null, 2), { mode: 0o600 });
    await step('committee', () => runHiring(['committee', '--job', job, '--runs', runs, '--jules', file('jules.json'),
        '--input', file('committee_in.json'), '--out', file('committee.json'), '--report', file('report.md'),
        '--trace', file('trace.jsonl'), '--feed', file('feed.json'), '--live', file('live.json'),
        '--progress', progressFile], 'committee', signal));

    const c = readJson(file('committee.json'));
    return `Done: ${apps.length} application(s) → ${remi.counts.shortlisted} shortlisted → ${finalists.length} finalists → ` +
        `top 5 ${c?.top5?.join(', ') || '?'} (confidence ${c?.report?.recommendation?.confidence || '?'}).`;
}
