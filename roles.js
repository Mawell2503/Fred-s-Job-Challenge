// Job roles + the agents that work on them.
//   GET    /api/roles                          (public) open roles, for the application form
//   GET    /api/admin/roles                    (admin)  all roles, with applicant counts and agent states
//   POST   /api/admin/roles                    (admin)  { title } -> new role
//   PATCH  /api/admin/roles/:id                (admin)  { title, status, adText, mustText, niceText, job, rules, logistics }
//   POST   /api/admin/roles/:id/clara          (admin)  CLARA proposes criteria (admin reviews, then PATCHes job)
//   POST   /api/admin/roles/:id/oscar          (admin)  re-process this role's CVs with its keep/drop settings
//   POST   /api/admin/roles/:id/remi           (admin)  { llmGate } must-have gate + top 20
//   POST   /api/admin/roles/:id/jules          (admin)  full read of the 20 + top 5
//   GET    /api/admin/roles/:id/results        (admin)  remi.json, jules.json, live progress
// Agents run in the background: the POST returns at once and the page polls /results.
import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { runPython } from './pyRunner.js';
import { RUNS_DIR } from './cvRunner.js';
import { cleanLogistics, publicRoleInfo } from './practical.js';
import { applicantsForRole, reprocessRole } from './apply.js';
import {
    listRoles, getRole, createRole, saveRoles, roleDir, cleanJob, cleanRules,
} from './rolesStore.js';

const MINUTES = 60_000;
const TIMEOUTS = { clara: 5 * MINUTES, remi: 30 * MINUTES, jules: 60 * MINUTES };
const readJson = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; } };

function lastLine(text) {
    return String(text).trim().split('\n').filter(Boolean).pop() || '';
}

/** Start `work` for one agent of one role unless something is already running for that role. */
function startAgent(role, agent, work) {
    role.agents ||= {};
    if (Object.values(role.agents).some(a => a.state === 'running')) {
        return 'Another agent is still running for this role. Wait for it to finish.';
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

async function runHiring(args, agent) {
    const { code, stdout, stderr } = await runPython(['-m', 'hiring', ...args], TIMEOUTS[agent]);
    if (code !== 0) throw new Error(lastLine(stderr) || `${agent} failed (exit ${code}).`);
    return lastLine(stdout);
}

function writeJob(role) {
    const file = path.join(roleDir(role.id), 'job.json');
    fs.writeFileSync(file, JSON.stringify(role.job, null, 2));
    return file;
}

function summary(role) {
    const apps = applicantsForRole(role.id);
    const dir = roleDir(role.id);
    return {
        ...role,
        applicants: apps.length,
        readable: apps.filter(a => a.cvStatus !== 'failed').length,
        hasRemi: fs.existsSync(path.join(dir, 'remi.json')),
        hasJules: fs.existsSync(path.join(dir, 'jules.json')),
    };
}

export function roleRoutes({ requireAuth }) {
    const router = express.Router();
    const json = express.json({ limit: '300kb' });          // job ads can be long

    router.get('/api/roles', (req, res) => {
        res.json(listRoles().filter(r => r.status === 'open').map(publicRoleInfo));
    });

    router.get('/api/admin/roles', requireAuth, (req, res) => res.json(listRoles().map(summary)));

    router.post('/api/admin/roles', requireAuth, json, (req, res) => {
        const title = String(req.body?.title ?? '').trim().slice(0, 120);
        if (!title) return res.status(400).json({ error: 'What profession are you looking for? Give the role a title.' });
        res.status(201).json(summary(createRole(title)));
    });

    // every route below works on one existing role
    router.use('/api/admin/roles/:id', requireAuth, (req, res, next) => {
        req.role = getRole(req.params.id);
        if (!req.role) return res.status(404).json({ error: 'Unknown role.' });
        next();
    });

    router.patch('/api/admin/roles/:id', json, (req, res) => {
        const role = req.role, b = req.body || {};
        if (b.title !== undefined) {
            const t = String(b.title).trim().slice(0, 120);
            if (!t) return res.status(400).json({ error: 'The title cannot be empty.' });
            role.title = t;
            role.job.title = t;
        }
        if (b.status !== undefined) role.status = b.status === 'closed' ? 'closed' : 'open';
        for (const k of ['adText', 'mustText', 'niceText']) {
            if (b[k] !== undefined) role[k] = String(b[k]).slice(0, k === 'adText' ? 20000 : 5000);
        }
        if (b.job !== undefined) {
            role.job = cleanJob(b.job, role.title);
            role.jobUpdatedAt = new Date().toISOString();
        }
        if (b.rules !== undefined) role.rules = cleanRules(b.rules);
        if (b.logistics !== undefined) role.logistics = cleanLogistics(b.logistics);
        saveRoles();
        res.json(summary(role));
    });

    router.post('/api/admin/roles/:id/clara', (req, res) => {
        const role = req.role;
        if (!(role.adText.trim() || role.mustText.trim() || role.niceText.trim())) {
            return res.status(400).json({ error: 'Paste the job description or write the must-haves first.' });
        }
        const busy = startAgent(role, 'clara', async () => {
            const dir = roleDir(role.id);
            const input = path.join(dir, 'clara_in.json'), out = path.join(dir, 'clara_out.json');
            fs.writeFileSync(input, JSON.stringify({ title: role.title, ad_text: role.adText, must_text: role.mustText, nice_text: role.niceText }));
            const msg = await runHiring(['clara', '--input', input, '--out', out], 'clara');
            role.claraProposal = { ...readJson(out), proposedAt: new Date().toISOString() };
            return msg + ' Review them, then save.';
        });
        if (busy) return res.status(409).json({ error: busy });
        res.status(202).json({ started: true });
    });

    router.post('/api/admin/roles/:id/oscar', (req, res) => {
        const role = req.role;
        if (!applicantsForRole(role.id).length) return res.status(400).json({ error: 'No applicants for this role yet.' });
        const busy = startAgent(role, 'oscar', async progressFile => {
            const r = await reprocessRole(role.id, role.rules, (done, total) =>
                fs.writeFileSync(progressFile, JSON.stringify({ stage: 'oscar', done, total })));
            return `Oscar re-processed ${r.total} CV(s)${r.failed ? `, ${r.failed} could not be read` : ''}.`;
        });
        if (busy) return res.status(409).json({ error: busy });
        res.status(202).json({ started: true });
    });

    router.post('/api/admin/roles/:id/remi', json, (req, res) => {
        const role = req.role;
        const ids = applicantsForRole(role.id).filter(a => a.cvStatus !== 'failed').map(a => a.candidateId);
        if (!ids.length) return res.status(400).json({ error: 'No readable CVs for this role yet.' });
        if (!role.job.must_haves.length && !role.job.nice_to_haves.length) {
            return res.status(400).json({ error: 'Save at least one criterion (ask Clara, or add them yourself) first.' });
        }
        const llmGate = req.body?.llmGate === true;
        const busy = startAgent(role, 'remi', async progressFile => {
            const dir = roleDir(role.id);
            fs.rmSync(path.join(dir, 'jules.json'), { force: true });      // a new shortlist makes the old top 5 stale
            const args = ['remi', '--job', writeJob(role), '--runs', RUNS_DIR, '--ids', ids.join(','),
                '--out', path.join(dir, 'remi.json'), '--progress', progressFile];
            if (llmGate) args.push('--llm-gate');
            return runHiring(args, 'remi');
        });
        if (busy) return res.status(409).json({ error: busy });
        res.status(202).json({ started: true });
    });

    router.post('/api/admin/roles/:id/jules', (req, res) => {
        const role = req.role, dir = roleDir(role.id);
        if (!fs.existsSync(path.join(dir, 'remi.json'))) return res.status(400).json({ error: 'Run Remi first.' });
        const busy = startAgent(role, 'jules', progressFile => runHiring(['jules', '--job', writeJob(role),
            '--runs', RUNS_DIR, '--remi', path.join(dir, 'remi.json'), '--out', path.join(dir, 'jules.json'),
            '--progress', progressFile], 'jules'));
        if (busy) return res.status(409).json({ error: busy });
        res.status(202).json({ started: true });
    });

    router.get('/api/admin/roles/:id/results', (req, res) => {
        const dir = roleDir(req.role.id);
        res.json({
            role: summary(req.role),
            progress: readJson(path.join(dir, 'progress.json')),
            remi: readJson(path.join(dir, 'remi.json')),
            jules: readJson(path.join(dir, 'jules.json')),
        });
    });

    // errors in these routes come back as JSON the admin page can show
    router.use((err, req, res, next) => {
        if (!/^\/api\/(admin\/)?roles/.test(req.originalUrl)) return next(err);
        if (err.status && err.status < 500) return res.status(err.status).json({ error: 'Invalid request.' });
        console.error(err);
        res.status(500).json({ error: 'Something went wrong. Please try again.' });
    });

    return router;
}
