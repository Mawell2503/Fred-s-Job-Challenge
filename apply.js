// Applicant upload -> CV reader (OSCAR) -> stored result.
//   POST /api/apply                         (public)  multipart form: roleId ("open" or empty = open
//                                                     application), name, linkedinUrl, resume (file),
//                                                     linkedin (optional LinkedIn PDF), practical (JSON
//                                                     string, required, for Nina). Everything joins the CV pool.
//   GET  /api/admin/applicants[?role=R001][&batch=B003][&archived=only|all][&details=1][&q=][&offset=&limit=]  (admin)
//                                                     the CV pool (archived CVs left out unless archived=). role=:
//                                                     only who that role's run reads (its scope), each with
//                                                     `application` (applied for this role / open / other roles /
//                                                     uploaded in a batch). batch=: one batch. details=1: with
//                                                     practical answers. q=: name, id or file name.
//                                                     limit=: { total, items } page.
//   GET  /api/admin/applicants/:id/cv       (admin)   the CV file exactly as uploaded
//   GET  /api/admin/applicants/:id/text     (admin)   the cleaned CV text block (kept sections)
//   GET  /api/admin/applicants/:id/full     (admin)   every section, personal details removed (what Jules reads)
//   GET  /api/admin/applicants/:id/record   (admin)   Oscar's structured record (JSON)
//   GET  /api/admin/applicants/:id/practical (admin)  the practical answers (JSON)
//   GET  /api/admin/applicants/:id/linkedin (admin)   the LinkedIn PDF they uploaded
import express from 'express';
import multer from 'multer';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { getRole } from './rolesStore.js';
import { applicationLabel } from './pipeline.js';
import { cleanPractical } from './practical.js';
import {
    DATA_DIR, UPLOAD_DIR, activeApplicants, applicantsInScope, cleanFileName, findApplicant, listApplicants,
    submitApplication,
} from './applicants.js';
import { getBatch } from './batches.js';


const ALLOWED = { resume: new Set(['.pdf', '.docx', '.txt']), linkedin: new Set(['.pdf']) };
const upload = multer({
    defParamCharset: 'utf8',                     // "Amélie_CV.pdf" keeps its accent (the default reads it as latin1)
    storage: multer.diskStorage({
        destination: UPLOAD_DIR,
        filename: (req, file, cb) => cb(null, crypto.randomUUID() + path.extname(file.originalname).toLowerCase()),
    }),
    limits: { fileSize: 5 * 1024 * 1024, files: 2 },
    fileFilter: (req, file, cb) => {
        const ok = ALLOWED[file.fieldname]?.has(path.extname(file.originalname).toLowerCase());
        if (ok) return cb(null, true);
        cb(new Error(file.fieldname === 'linkedin'
            ? 'Only PDF files are accepted for the LinkedIn profile (use Resources > Save to PDF on LinkedIn).'
            : 'Only PDF, DOCX or TXT files are accepted.'));
    },
});

export const cleanUrl = v => {
    let s = String(v ?? '').trim();
    if (!s) return '';
    // "linkedin.com/in/name" or "www.linkedin.com/..." without http(s):// is what most people type
    if (!/^[a-z][a-z0-9+.-]*:/i.test(s) && /^[\w-]+(\.[\w-]+)+(\/|$)/.test(s)) s = `https://${s}`;
    try { return ['http:', 'https:'].includes(new URL(s).protocol) ? s : null; } catch { return null; }
};

const isPdf = file => {
    try {
        const fd = fs.openSync(file, 'r');
        const head = Buffer.alloc(1024);
        fs.readSync(fd, head, 0, 1024, 0);
        fs.closeSync(fd);
        return head.includes('%PDF');
    } catch { return false; }
};

export function applyRoutes({ requireAuth }) {
    const router = express.Router();

    router.post('/api/apply', upload.fields([{ name: 'resume', maxCount: 1 }, { name: 'linkedin', maxCount: 1 }]),
        async (req, res, next) => {
            const cv = req.files?.resume?.[0] || null;
            const li = req.files?.linkedin?.[0] || null;
            const discard = () => { for (const f of [cv, li]) if (f) fs.rmSync(f.path, { force: true }); };
            try {
                const wanted = String(req.body?.roleId ?? '').trim();
                const open = !wanted || wanted === 'open';                   // "Open application: any position"
                const role = open ? null : getRole(wanted);
                const name = String(req.body?.name ?? '').trim();
                const instagramUrl = cleanUrl(req.body?.instagramUrl);
                const linkedinUrl = cleanUrl(req.body?.linkedinUrl);
                // a rejected application must not leave its uploads behind
                const refuse = error => { discard(); return res.status(400).json({ error }); };
                if (!open && (!role || role.status !== 'open')) return refuse('That position is no longer open. Choose another one, or send an open application.');
                if (!name || name.length > 100) return refuse('Enter your name.');
                if (instagramUrl === null || linkedinUrl === null) return refuse('Links must start with http:// or https://');
                if (!cv) return refuse('Choose a CV file (PDF, DOCX or TXT).');
                if (li && !isPdf(li.path)) return refuse('The LinkedIn file is not a PDF. On LinkedIn: Resources > Save to PDF.');
                const practical = cleanPractical(req.body?.practical, role);
                if (practical.error) return refuse(practical.error);

                const out = await submitApplication({
                    roleId: role?.id || null, cvPath: cv.path, cvName: cv.originalname, linkedinPath: li?.path || null,
                    name, linkedinUrl, instagramUrl, practical: practical.value,
                });
                if (out.duplicate) {
                    discard();
                    return res.status(409).json({ error: open ? 'We already have this CV. No need to send it again.'
                        : 'We already have this CV for this position. No need to send it again.' });
                }
                if (out.restored) {
                    discard();                                  // an archived CV sent again: it is back in the pool
                    return res.status(200).json({ received: true });
                }
                if (out.added) {
                    discard();                                  // the CV we have is kept; nothing new to store
                    return res.status(200).json({ received: true, added: true,
                        message: 'We already had your CV: this position has been added to your application.' });
                }
                if (out.result.status === 'failed') {
                    discard();                                  // nothing was recorded: keep no files
                    return res.status(422).json({ error: out.result.codes?.includes('PASSWORD_PROTECTED')
                        ? 'That PDF is password-protected. Please upload a copy without a password.'
                        : 'We could not read that file. Please upload a text-based PDF, a DOCX or a TXT.' });
                }
                res.status(201).json({ received: true });
            } catch (err) {
                // nothing was recorded for this upload, so don't keep the files
                if (cv && !listApplicants().some(a => a.cvFile === path.basename(cv.path))) discard();
                next(err);
            }
        });

    router.get('/api/admin/applicants', requireAuth, (req, res) => {
        const role = req.query.role ? getRole(String(req.query.role)) : null;
        if (req.query.role && !role) return res.status(404).json({ error: 'Unknown role.' });
        const details = req.query.details === '1';
        const archived = String(req.query.archived ?? '');
        let list = role ? applicantsInScope(role)
            : archived === 'all' ? listApplicants() : archived === 'only' ? listApplicants().filter(a => a.archived) : activeApplicants();
        if (req.query.batch) list = list.filter(a => a.batch === String(req.query.batch));
        const q = String(req.query.q ?? '').trim().toLowerCase().slice(0, 100);
        if (q) list = list.filter(a => [a.name, a.candidateId, a.cvName].some(v => String(v ?? '').toLowerCase().includes(q)));
        const view = ({ candidateId, appliedFor, name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes, cvFile, cvName,
            linkedinFile, practical, source, batch, archived: isArchived }) => ({
            id: candidateId, appliedFor: appliedFor.map(id => ({ id, title: getRole(id)?.title || id })),
            name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes,
            cvName: cvName || cleanFileName('', cvFile),
            hasLinkedinPdf: Boolean(linkedinFile), hasPractical: Boolean(practical), source: source || 'form',
            batch: batch ? { id: batch, name: getBatch(batch)?.name || batch } : null, archived: Boolean(isArchived),
            ...(role ? { application: applicationLabel({ appliedFor }, role) } : {}),
            ...(details ? { practical: practical || null } : {}),
        });
        if (req.query.limit === undefined) return res.json(list.map(view));
        // one page, newest first: { total, items }
        const limit = Math.min(200, Math.max(1, Number.parseInt(req.query.limit, 10) || 50));
        const offset = Math.max(0, Number.parseInt(req.query.offset, 10) || 0);
        const page = list.slice().reverse().slice(offset, offset + limit);
        res.json({ total: list.length, offset, limit, items: page.map(view) });
    });

    // The CV file exactly as the applicant uploaded it. PDF and TXT open in the browser, DOCX downloads.
    // nosniff (+ sandbox for text): an uploaded file can never run as a page on this site. No sandbox on
    // PDFs: Chrome refuses to show a PDF under a sandbox CSP, and its PDF viewer runs no site script anyway.
    const CV_TYPES = { '.pdf': 'application/pdf', '.txt': 'text/plain; charset=utf-8',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };
    router.get('/api/admin/applicants/:id/cv', requireAuth, (req, res) => {
        const a = findApplicant(req.params.id);
        const ext = a ? path.extname(a.cvFile).toLowerCase() : '';
        const file = a ? path.join(UPLOAD_DIR, path.basename(a.cvFile)) : '';
        if (!a || !CV_TYPES[ext] || !fs.existsSync(file)) return res.status(404).json({ error: 'No CV file for this applicant.' });
        const name = a.cvName || cleanFileName('', a.cvFile);
        res.set({
            'Content-Type': CV_TYPES[ext],
            'Content-Disposition': `${ext === '.docx' ? 'attachment' : 'inline'}; filename="${name.replace(/[^\x20-\x7e]/g, '_')}"; filename*=UTF-8''${encodeURIComponent(name)}`,
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, no-store',
        });
        if (ext !== '.pdf') res.set('Content-Security-Policy', 'sandbox');
        // a read error (file removed meanwhile) must end this response, not crash the server
        fs.createReadStream(file)
            .on('error', err => { console.error(`CV ${a.candidateId}:`, err.message); res.headersSent ? res.destroy() : res.status(404).json({ error: 'No CV file for this applicant.' }); })
            .pipe(res);
    });

    // Oscar's files for one applicant: text (kept sections), full (all sections), record (JSON)
    const OUTPUTS = { text: ['cv_text', '.txt', 'text/plain'], full: ['cv_full', '.txt', 'text/plain'], record: ['cv_json', '.json', 'application/json'] };
    router.get('/api/admin/applicants/:id/:kind', requireAuth, (req, res) => {
        const a = findApplicant(req.params.id);   // exact match: no path tricks
        if (!a) return res.status(404).json({ error: 'Unknown applicant.' });
        if (req.params.kind === 'practical') {
            return a.practical ? res.json(a.practical) : res.status(404).json({ error: 'No practical details for this applicant.' });
        }
        if (req.params.kind === 'linkedin') {
            const li = a.linkedinFile ? path.join(UPLOAD_DIR, path.basename(a.linkedinFile)) : '';
            if (!li || !fs.existsSync(li)) return res.status(404).json({ error: 'No LinkedIn PDF for this applicant.' });
            res.set({ 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store' });
            return res.type('application/pdf').sendFile(li, err => {
                if (err && !res.headersSent) res.status(404).json({ error: 'No LinkedIn PDF for this applicant.' });
            });
        }
        const out = OUTPUTS[req.params.kind];
        if (!out) return res.status(404).json({ error: 'Unknown applicant.' });
        const file = path.join(DATA_DIR, 'runs', a.candidateId, out[0], `${a.candidateId}${out[1]}`);
        if (!fs.existsSync(file)) return res.status(404).json({ error: 'No output for this applicant.' });
        res.type(out[2]).send(fs.readFileSync(file, 'utf8'));
    });

    // upload problems (too big, wrong type) come back as JSON, not an HTML error page
    router.use((err, req, res, next) => {
        if (err instanceof multer.MulterError || err.message?.startsWith('Only PDF')) {
            return res.status(400).json({ error: err.code === 'LIMIT_FILE_SIZE' ? 'File is too large (max 5 MB).' : err.message });
        }
        console.error(err);
        res.status(500).json({ error: 'Something went wrong. Please try again.' });
    });

    return router;
}
