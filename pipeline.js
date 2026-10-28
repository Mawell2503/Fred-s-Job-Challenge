// The full pipeline for one role: Oscar -> Remi -> Jules -> Committee (+ Gaston), in the background.
// Clara stays manual on purpose: a person must approve the criteria first.
//
//   runPipeline(role, { llmGate }, progressFile)  -> summary message (throws with the failing step)
//
// No express import: used by roles.js (the "Run full pipeline" button) and by
// scripts/run-pipeline.js (the same thing from the terminal).
//
// Data flow worth knowing:
//   - Oscar only re-reads CVs whose keep/drop settings changed since they were last read.
//   - The practical answers (Nina) and LinkedIn PDFs (Helene) never enter Oscar / Remi / Jules:
//     after Jules has picked the top 10, only THOSE 10 candidates' answers are written into
//     committee_in.json and handed to the committee.
import fs from 'node:fs';
import path from 'node:path';
import { runPython } from './pyRunner.js';
import { RUNS_DIR } from './cvRunner.js';
import { applicantsForRole, reprocessRole } from './applicants.js';
import { roleDir, saveRoles } from './rolesStore.js';

const MINUTES = 60_000;
export const TIMEOUTS = { clara: 5 * MINUTES, remi: 30 * MINUTES, jules: 60 * MINUTES, committee: 90 * MINUTES };
export const STEPS = ['oscar', 'remi', 'jules', 'committee'];
export const STEP_LABEL = { oscar: 'Oscar (CVs)', remi: 'Remi (top 20)', jules: 'Jules (top 10)', committee: 'Committee + Gaston' };
// everything a pipeline run produces; deleted at the start of a run so nothing stale is ever shown
export const OUTPUTS = ['remi.json', 'jules.json', 'committee_in.json', 'committee.json', 'report.md', 'trace.jsonl', 'feed.json'];

export const readJson = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; } };
const lastLine = text => String(text).trim().split('\n').filter(Boolean).pop() || '';

/** Start `work` for one agent of one role unless something is already running for that role. */
export function startAgent(role, agent, work) {
    role.agents ||= {};
    if (Object.values(role.agents).some(a => a.state === 'running')) {
        return 'Something is already running for this role. Wait for it to finish.';
    }
    role.agents[agent] = { state: 'running', startedAt: new Date().toISOString(), message: '' };
    saveRoles();
    const progressFile = path.join(roleDir(role.id), 'progress.json');
    fs.rmSync(progressFile, { force: true });
    Promise.resolve()
        .then(() => work(progressFile))
        .then(message => Object.assign(role.agents[agent], { state: 'done', message: message || '' }))
        .catch(err => Object.assign(role.agents[agent], { state: 'failed', message: String(err.message || err).slice(0, 800) }))
        .finally(() => {
            role.agents[agent].finishedAt = new Date().toISOString();
            saveRoles();
        });
    return null;
}

export async function runHiring(args, agent) {
    const { code, stdout, stderr } = await runPython(['-m', 'hiring', ...args], TIMEOUTS[agent]);
    if (code !== 0) throw new Error(lastLine(stderr) || `${agent} failed (exit ${code}).`);
    return lastLine(stdout);
}

export function writeJob(role) {
    const file = path.join(roleDir(role.id), 'job.json');
    fs.writeFileSync(file, JSON.stringify(role.job, null, 2));
    return file;
}

/** Why the pipeline can't start yet, or null. */
export function pipelineProblem(role) {
    if (!role.job?.must_haves?.length && !role.job?.nice_to_haves?.length) {
        return 'Save the criteria first (ask Clara, check them, then "Save criteria").';
    }
    const apps = applicantsForRole(role.id);
    if (!apps.length) return 'No applications for this role yet.';
    return null;
}

export async function runPipeline(role, { llmGate = false } = {}, progressFile) {
    const dir = roleDir(role.id);
    const file = name => path.join(dir, name);
    progressFile ||= file('progress.json');
    const progress = data => fs.writeFileSync(progressFile, JSON.stringify(data));
    for (const f of OUTPUTS) fs.rmSync(file(f), { force: true });
    role.agents ||= {};
    for (const s of STEPS) delete role.agents[s];
    saveRoles();

    const step = async (name, fn) => {
        role.agents[name] = { state: 'running', startedAt: new Date().toISOString(), message: '' };
        saveRoles();
        progress({ stage: name, done: 0, total: 0 });
        try {
            const message = await fn();
            Object.assign(role.agents[name], { state: 'done', message: message || '', finishedAt: new Date().toISOString() });
            saveRoles();
            return message;
        } catch (err) {
            Object.assign(role.agents[name], { state: 'failed', message: String(err.message || err).slice(0, 800),
                finishedAt: new Date().toISOString() });
            saveRoles();
            throw new Error(`${STEP_LABEL[name]} failed: ${err.message || err}`);
        }
    };

    // 1. Oscar: only CVs read with older keep/drop settings
    await step('oscar', async () => {
        const r = await reprocessRole(role.id, role.rules, (done, total) => progress({ stage: 'oscar', done, total }),
            { onlyStale: true });
        return r.total ? `Re-read ${r.total} CV(s) with the current sections${r.failed ? `, ${r.failed} unreadable` : ''}.`
            : 'Every CV was already read with the current sections.';
    });
    const apps = applicantsForRole(role.id);
    const ids = apps.filter(a => a.cvStatus !== 'failed').map(a => a.candidateId);
    if (!ids.length) throw new Error('No readable CVs for this role.');
    const job = writeJob(role);

    // 2. Remi: must-have gate + top 20
    await step('remi', () => {
        const args = ['remi', '--job', job, '--runs', RUNS_DIR, '--ids', ids.join(','), '--out', file('remi.json'),
            '--progress', progressFile];
        if (llmGate) args.push('--llm-gate');
        return runHiring(args, 'remi');
    });
    const remi = readJson(file('remi.json'));
    if (!remi?.shortlist?.length) throw new Error('Remi shortlisted nobody: every candidate failed a must-have.');

    // 3. Jules: full read of the 20, keeps 10
    await step('jules', () => runHiring(['jules', '--job', job, '--runs', RUNS_DIR, '--remi', file('remi.json'),
        '--out', file('jules.json'), '--top', '10', '--progress', progressFile], 'jules'));
    const jules = readJson(file('jules.json'));
    const finalists = (jules?.finalists || []).map(f => f.candidate_id);
    if (!finalists.length) throw new Error('Jules produced no finalists.');

    // 4. Committee: ONLY now do the practical answers and LinkedIn flags of the 10 come in
    const byId = Object.fromEntries(apps.map(a => [a.candidateId, a]));
    const cin = {
        role: { id: role.id, title: role.title },
        logistics: role.logistics || {},
        practical: Object.fromEntries(finalists.filter(c => byId[c]?.practical).map(c => [c, byId[c].practical])),
        linkedin: Object.fromEntries(finalists.map(c => [c, Boolean(byId[c]?.linkedinFile)])),
        funnel: { applications: apps.length, readable: ids.length, passed_gate: remi.counts?.passed_gate,
            read_by_jules: jules.counts?.read },
    };
    fs.writeFileSync(file('committee_in.json'), JSON.stringify(cin, null, 2), { mode: 0o600 });
    await step('committee', () => runHiring(['committee', '--job', job, '--runs', RUNS_DIR, '--jules', file('jules.json'),
        '--input', file('committee_in.json'), '--out', file('committee.json'), '--report', file('report.md'),
        '--trace', file('trace.jsonl'), '--feed', file('feed.json'), '--progress', progressFile], 'committee'));

    const c = readJson(file('committee.json'));
    return `Done: ${apps.length} application(s) → ${remi.counts.shortlisted} shortlisted → ${finalists.length} finalists → ` +
        `top 5 ${c?.top5?.join(', ') || '?'} (confidence ${c?.report?.recommendation?.confidence || '?'}).`;
}
