// Job roles + the agents that work on them.
//   GET    /api/roles                          (public) open roles, for the application form
//   GET    /api/admin/roles                    (admin)  all roles, with applicant counts and agent states
//   POST   /api/admin/roles                    (admin)  { title } -> new role
//   PATCH  /api/admin/roles/:id                (admin)  { title, status, adText, mustText, niceText, job, rules, logistics,
//                                                        scope: 'pool' | 'direct' | 'batches', batches: [batchIds],
//                                                        tieBreak: boolean }
//   POST   /api/admin/roles/:id/clara          (admin)  CLARA proposes criteria (admin reviews, then PATCHes job)
//   POST   /api/admin/roles/:id/pipeline       (admin)  { llmGate } Oscar -> Remi -> Jules -> Committee -> Gaston
//   GET    /api/admin/roles/:id/results        (admin)  every step's output + live progress + debate feed
//   GET    /api/admin/roles/:id/report.md      (admin)  Gaston's report as a Markdown download
// Agents run in the background: the POST returns at once and the page polls /results.
import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { cleanLogistics, publicRoleInfo } from './practical.js';
import { activeApplicants, applicantsInScope, directApplicants, findApplicant } from './applicants.js';
import { getBatch } from './batches.js';
import { staleCount, usesArrivalReads } from './reads.js';
import {
    listRoles, getRole, createRole, saveRoles, roleDir, cleanJob, cleanRules, SCOPES,
} from './rolesStore.js';
import { applicationLabel, pipelineProblem, readJson, runHiring, runPipeline, startAgent } from './pipeline.js';

function summary(role) {
    const apps = applicantsInScope(role);
    const dir = roleDir(role.id);
    let first = null, last = null;
    for (const a of apps) {
        if (!first || a.appliedAt < first) first = a.appliedAt;
        if (!last || a.appliedAt > last) last = a.appliedAt;
    }
    return {
        ...role,
        applicants: apps.length,                                // who a run reads (role.scope)
        readable: apps.filter(a => a.cvStatus !== 'failed').length,
        pool: activeApplicants().length,                        // the whole CV pool (archived CVs left out)
        direct: directApplicants(role.id).length,               // people who chose this role on the form
        appliedFrom: first, appliedTo: last,
        ownReads: !usesArrivalReads(role),                      // its CV sections differ from the default
        toRead: staleCount(role, apps),                         // CVs Oscar must (re)read before the next run
        hasRemi: fs.existsSync(path.join(dir, 'remi.json')),
        hasJules: fs.existsSync(path.join(dir, 'jules.json')),
        hasReport: fs.existsSync(path.join(dir, 'report.md')),
        pipelineProblem: pipelineProblem(role),
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
        if (b.scope !== undefined) role.scope = SCOPES.includes(b.scope) ? b.scope : 'pool';
        if (b.batches !== undefined) {
            role.batches = [...new Set((Array.isArray(b.batches) ? b.batches : []).map(String))].filter(id => getBatch(id)).slice(0, 200);
        }
        if (b.tieBreak !== undefined) role.tieBreak = b.tieBreak === true;
        saveRoles();
        res.json(summary(role));
    });

    // Clara stays manual: the admin must check and save her criteria before anything else runs
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

    // Everything after Clara, in one go (replaces the old per-agent buttons)
    router.post('/api/admin/roles/:id/pipeline', json, (req, res) => {
        const role = req.role;
        const problem = pipelineProblem(role);
        if (problem) return res.status(400).json({ error: problem });
        const llmGate = req.body?.llmGate === true;
        const busy = startAgent(role, 'pipeline', progressFile => runPipeline(role, { llmGate }, progressFile));
        if (busy) return res.status(409).json({ error: busy });
        res.status(202).json({ started: true });
    });

    router.get('/api/admin/roles/:id/results', (req, res) => {
        const dir = roleDir(req.role.id);
        const committee = readJson(path.join(dir, 'committee.json'));
        const jules = readJson(path.join(dir, 'jules.json'));
        // what each finalist applied for, towards this role (the badges on the lobby cards), and the name on
        // their application (Oscar's name from the CV is only the fallback: some CVs carry none)
        const application = {}, names = {};
        for (const f of jules?.finalists || []) {
            const a = findApplicant(f.candidate_id);
            if (!a) continue;
            application[f.candidate_id] = applicationLabel(a, req.role);
            if (a.name) names[f.candidate_id] = a.name;
        }
        res.json({
            role: summary(req.role),
            application,
            names,
            progress: readJson(path.join(dir, 'progress.json')),
            remi: readJson(path.join(dir, 'remi.json')),
            jules,
            committee,
            // live debate feed while the committee runs (committee.json carries it once finished)
            feed: committee?.feed || readJson(path.join(dir, 'feed.json')) || [],
            // the committee page's live state: phase, who speaks, positions, each finalist's facts (live.py)
            live: readJson(path.join(dir, 'live.json')),
        });
    });

    router.get('/api/admin/roles/:id/report.md', (req, res) => {
        const file = path.join(roleDir(req.role.id), 'report.md');
        if (!fs.existsSync(file)) return res.status(404).json({ error: 'No report yet: run the pipeline.' });
        res.attachment(`committee-report-${req.role.id}.md`).type('text/markdown').send(fs.readFileSync(file, 'utf8'));
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
