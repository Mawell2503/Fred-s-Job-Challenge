// Applicant upload -> CV reader (OSCAR) -> stored result.
//   POST /api/apply                         (public)  multipart form: roleId, name, linkedinUrl,
//                                                     resume (file), linkedin (optional LinkedIn PDF),
//                                                     practical (JSON string, required, for Nina)
//   GET  /api/admin/applicants[?role=R001]  (admin)   list of applicants + CV status
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
import { cleanPractical } from './practical.js';
import {
    DATA_DIR, UPLOAD_DIR, applicantsForRole, findApplicant, listApplicants, submitApplication,
} from './applicants.js';


const ALLOWED = { resume: new Set(['.pdf', '.docx', '.txt']), linkedin: new Set(['.pdf']) };
const upload = multer({
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

const cleanUrl = v => {
    const s = String(v ?? '').trim();
    if (!s) return '';
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
                const role = getRole(String(req.body?.roleId ?? ''));
                const name = String(req.body?.name ?? '').trim();
                const instagramUrl = cleanUrl(req.body?.instagramUrl);
                const linkedinUrl = cleanUrl(req.body?.linkedinUrl);
                // a rejected application must not leave its uploads behind
                const refuse = error => { discard(); return res.status(400).json({ error }); };
                if (!role || role.status !== 'open') return refuse('Choose the position you are applying for.');
                if (!name || name.length > 100) return refuse('Enter your name.');
                if (instagramUrl === null || linkedinUrl === null) return refuse('Links must start with http:// or https://');
                if (!cv) return refuse('Choose a CV file (PDF, DOCX or TXT).');
                if (li && !isPdf(li.path)) return refuse('The LinkedIn file is not a PDF. On LinkedIn: Resources > Save to PDF.');
                const practical = cleanPractical(req.body?.practical, role);
                if (practical.error) return refuse(practical.error);

                const out = await submitApplication({
                    role, cvPath: cv.path, linkedinPath: li?.path || null, name, linkedinUrl, instagramUrl,
                    practical: practical.value,
                });
                if (out.duplicate) {
                    discard();
                    return res.status(409).json({ error: 'We already have this CV for this position. No need to send it again.' });
                }
                if (out.result.status === 'failed') {
                    return res.status(422).json({ error: 'We could not read that file. Please upload a text-based PDF, a DOCX or a TXT.' });
                }
                res.status(201).json({ received: true });
            } catch (err) {
                // nothing was recorded for this upload, so don't keep the files
                if (cv && !listApplicants().some(a => a.cvFile === path.basename(cv.path))) discard();
                next(err);
            }
        });

    router.get('/api/admin/applicants', requireAuth, (req, res) => {
        const role = req.query.role ? String(req.query.role) : null;
        res.json((role ? applicantsForRole(role) : listApplicants())
            .map(({ candidateId, roleId, name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes, linkedinFile, practical }) =>
                ({ id: candidateId, roleId, name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes,
                    hasLinkedinPdf: Boolean(linkedinFile), hasPractical: Boolean(practical) })));
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
            if (!a.linkedinFile) return res.status(404).json({ error: 'No LinkedIn PDF for this applicant.' });
            return res.type('application/pdf').sendFile(path.join(UPLOAD_DIR, a.linkedinFile));
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
