// Applicant upload -> CV reader (OSCAR) -> stored result.
//   POST /api/apply                         (public)  multipart form: roleId, name, instagramUrl, linkedinUrl, resume
//   GET  /api/admin/applicants[?role=R001]  (admin)   list of applicants + CV status
//   GET  /api/admin/applicants/:id/text     (admin)   the cleaned CV text block (kept sections)
//   GET  /api/admin/applicants/:id/full     (admin)   every section, personal details removed (what Jules reads)
//   GET  /api/admin/applicants/:id/record   (admin)   Oscar's structured record (JSON)
import express from 'express';
import multer from 'multer';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { runCvReader } from './cvRunner.js';
import { getRole } from './rolesStore.js';

const DATA_DIR = path.resolve('data');
const UPLOAD_DIR = path.join(DATA_DIR, 'uploads');
const APPLICANTS_FILE = path.join(DATA_DIR, 'applicants.json');
fs.mkdirSync(UPLOAD_DIR, { recursive: true });

const ALLOWED = new Set(['.pdf', '.docx', '.txt']);
const upload = multer({
    storage: multer.diskStorage({
        destination: UPLOAD_DIR,
        filename: (req, file, cb) => cb(null, crypto.randomUUID() + path.extname(file.originalname).toLowerCase()),
    }),
    limits: { fileSize: 5 * 1024 * 1024, files: 1 },
    fileFilter: (req, file, cb) =>
        ALLOWED.has(path.extname(file.originalname).toLowerCase())
            ? cb(null, true)
            : cb(new Error('Only PDF, DOCX or TXT files are accepted.')),
});

let applicants = [];
try { applicants = JSON.parse(fs.readFileSync(APPLICANTS_FILE, 'utf8')); } catch { /* first run */ }

function saveApplicants() {
    const tmp = `${APPLICANTS_FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(applicants, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, APPLICANTS_FILE);
}

// One CV at a time: keeps numbering safe and the machine responsive.
let queue = Promise.resolve();
const inQueue = job => (queue = queue.then(job, job));

const cleanUrl = v => {
    const s = String(v ?? '').trim();
    if (!s) return '';
    try { return ['http:', 'https:'].includes(new URL(s).protocol) ? s : null; } catch { return null; }
};

/** Applicants of one role whose CV produced output (what Remi can work on). */
export const applicantsForRole = roleId => applicants.filter(a => a.roleId === roleId);

/**
 * Re-run Oscar on every CV of a role with the role's current keep/drop settings,
 * so a settings change also applies to CVs that are already in.
 */
export async function reprocessRole(roleId, rules, onProgress) {
    const list = applicantsForRole(roleId);
    let done = 0, failed = 0;
    for (const a of list) {
        await inQueue(async () => {
            try {
                const r = await runCvReader(path.join(UPLOAD_DIR, a.cvFile), a.number, rules);
                Object.assign(a, { cvStatus: r.status, cvCodes: r.codes, processedAt: new Date().toISOString() });
                if (r.status === 'failed') failed++;
            } catch (e) {
                failed++;
                Object.assign(a, { cvStatus: 'failed', cvCodes: ['REPROCESS_ERROR'] });
                console.error(`reprocess ${a.candidateId}:`, e.message);
            }
            saveApplicants();
        });
        onProgress?.(++done, list.length);
    }
    return { total: list.length, failed };
}

export function applyRoutes({ requireAuth }) {
    const router = express.Router();

    router.post('/api/apply', upload.single('resume'), async (req, res, next) => {
        const file = req.file;
        try {
            const role = getRole(String(req.body?.roleId ?? ''));
            const name = String(req.body?.name ?? '').trim();
            const instagramUrl = cleanUrl(req.body?.instagramUrl);
            const linkedinUrl = cleanUrl(req.body?.linkedinUrl);
            // a rejected application must not leave its upload behind
            const refuse = error => {
                if (file) fs.rmSync(file.path, { force: true });
                return res.status(400).json({ error });
            };
            if (!role || role.status !== 'open') return refuse('Choose the position you are applying for.');
            if (!name || name.length > 100) return refuse('Enter your name.');
            if (instagramUrl === null || linkedinUrl === null) return refuse('Links must start with http:// or https://');
            if (!file) return refuse('Choose a CV file (PDF, DOCX or TXT).');

            const result = await inQueue(async () => {
                const number = applicants.reduce((m, a) => Math.max(m, a.number), 0) + 1;
                const r = await runCvReader(file.path, number, role.rules);
                applicants.push({
                    number, candidateId: r.candidateId, roleId: role.id, name, instagramUrl, linkedinUrl,
                    appliedAt: new Date().toISOString(),
                    cvStatus: r.status, cvCodes: r.codes,
                    cvFile: path.basename(file.path),
                });
                saveApplicants();
                return r;
            });

            if (result.status === 'failed') {
                return res.status(422).json({ error: 'We could not read that file. Please upload a text-based PDF, a DOCX or a TXT.' });
            }
            res.status(201).json({ received: true });
        } catch (err) {
            next(err);
        }
    });

    router.get('/api/admin/applicants', requireAuth, (req, res) => {
        const role = req.query.role ? String(req.query.role) : null;
        res.json(applicants
            .filter(a => !role || a.roleId === role)
            .map(({ candidateId, roleId, name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes }) =>
                ({ id: candidateId, roleId, name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes })));
    });

    // Oscar's files for one applicant: text (kept sections), full (all sections), record (JSON)
    const OUTPUTS = { text: ['cv_text', '.txt', 'text/plain'], full: ['cv_full', '.txt', 'text/plain'], record: ['cv_json', '.json', 'application/json'] };
    router.get('/api/admin/applicants/:id/:kind', requireAuth, (req, res) => {
        const out = OUTPUTS[req.params.kind];
        const a = applicants.find(x => x.candidateId === req.params.id);   // exact match: no path tricks
        if (!out || !a) return res.status(404).json({ error: 'Unknown applicant.' });
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
