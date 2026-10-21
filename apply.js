// Applicant upload -> CV reader -> stored result.
//   POST /api/apply                       (public)  multipart form: name, instagramUrl, linkedinUrl, resume
//   GET  /api/admin/applicants            (admin)   list of applicants + CV status
//   GET  /api/admin/applicants/:id/text   (admin)   the cleaned CV text block for one applicant
import express from 'express';
import multer from 'multer';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { runCvReader } from './cvRunner.js';

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

let queue = Promise.resolve();
const inQueue = job => (queue = queue.then(job, job));

const cleanUrl = v => {
    const s = String(v ?? '').trim();
    if (!s) return '';
    try { return ['http:', 'https:'].includes(new URL(s).protocol) ? s : null; } catch { return null; }
};

export function applyRoutes({ requireAuth }) {
    const router = express.Router();

    router.post('/api/apply', upload.single('resume'), async (req, res, next) => {
        const file = req.file;
        try {
            const name = String(req.body?.name ?? '').trim();
            const instagramUrl = cleanUrl(req.body?.instagramUrl);
            const linkedinUrl = cleanUrl(req.body?.linkedinUrl);
            if (!name || name.length > 100) return res.status(400).json({ error: 'Enter your name.' });
            if (instagramUrl === null || linkedinUrl === null) return res.status(400).json({ error: 'Links must start with http:// or https://' });
            if (!file) return res.status(400).json({ error: 'Choose a CV file (PDF, DOCX or TXT).' });

            const result = await inQueue(async () => {
                const number = applicants.reduce((m, a) => Math.max(m, a.number), 0) + 1;
                const r = await runCvReader(file.path, number);
                applicants.push({
                    number, candidateId: r.candidateId, name, instagramUrl, linkedinUrl,
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
        res.json(applicants.map(({ number, candidateId, name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes }) =>
            ({ id: candidateId, name, instagramUrl, linkedinUrl, appliedAt, cvStatus, cvCodes })));
    });

    router.get('/api/admin/applicants/:id/text', requireAuth, (req, res) => {
        const a = applicants.find(x => x.candidateId === req.params.id);   // exact match: no path tricks
        if (!a) return res.status(404).json({ error: 'Unknown applicant.' });
        const file = path.join(DATA_DIR, 'runs', a.candidateId, 'cv_text', `${a.candidateId}.txt`);
        if (!fs.existsSync(file)) return res.status(404).json({ error: 'No CV text for this applicant.' });
        res.type('text/plain').send(fs.readFileSync(file, 'utf8'));
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