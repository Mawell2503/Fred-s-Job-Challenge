// CV batches (admin only). See batches.js (the batches) and batchOps.js (archive / restore / delete).
//   GET    /api/admin/batches                      every batch with its counts, the settings, and the weekly
//                                                  batches waiting for "keep, archive or delete?" (pending)
//   POST   /api/admin/batches                      { name } -> a new, empty batch to upload into
//   PATCH  /api/admin/batches/:id                  { name } rename
//   POST   /api/admin/batches/:id/cvs              multipart, ONE file "cv" (pdf/docx/txt, 5 MB): into this batch.
//                                                  The page sends the files one by one (progress, short requests).
//   POST   /api/admin/batches/:id/archive          archive every CV in it
//   POST   /api/admin/batches/:id/restore          restore every archived CV in it
//   DELETE /api/admin/batches/:id                  delete the batch and its CVs for good
//   POST   /api/admin/batches/:id/review           { action: keep | archive | delete | later } (weekly batches)
//   PUT    /api/admin/batches-settings             { reviewAfterDays: 7 | 14 | 30 }
//   POST   /api/admin/cvs/archive | restore | delete   { ids: ['C0001', ...] } single CVs, from a batch's list
import express from 'express';
import multer from 'multer';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { UPLOAD_DIR, submitApplication } from './applicants.js';
import { createBatch, getBatch, renameBatch, setReviewAfterDays } from './batches.js';
import {
    answerReview, archiveBatch, archiveCvs, batchSummaries, deleteBatch, deleteCvs, pendingReviews, restoreBatch,
    restoreCvs, settings,
} from './batchOps.js';

const TYPES = new Set(['.pdf', '.docx', '.txt']);
const upload = multer({
    defParamCharset: 'utf8',
    storage: multer.diskStorage({
        destination: UPLOAD_DIR,
        filename: (req, file, cb) => cb(null, crypto.randomUUID() + path.extname(file.originalname).toLowerCase()),
    }),
    limits: { fileSize: 5 * 1024 * 1024, files: 1 },
    fileFilter: (req, file, cb) => (TYPES.has(path.extname(file.originalname).toLowerCase())
        ? cb(null, true) : cb(new Error('Only PDF, DOCX or TXT files are accepted.'))),
});

/** "Jean-Paul_Moutou CV.pdf" -> "Jean-Paul Moutou CV": the name when Oscar finds none in the CV. */
const nameFromFile = original => path.parse(String(original || '')).name.replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 100) || 'Unnamed CV';

const ids = body => (Array.isArray(body?.ids) ? body.ids.map(String).slice(0, 100000) : []);
const reply = (res, out, ok = 200) => (out.error ? res.status(out.error.startsWith('A pipeline is running') ? 409 : 400).json(out) : res.status(ok).json(out));

export function batchRoutes({ requireAuth }) {
    const router = express.Router();
    const json = express.json({ limit: '2mb' });            // a list of thousands of candidate ids

    router.use(['/api/admin/batches', '/api/admin/batches-settings', '/api/admin/cvs'], requireAuth);

    router.get('/api/admin/batches', (req, res) => {
        res.json({ batches: batchSummaries(), pending: pendingReviews(), settings: settings() });
    });

    router.post('/api/admin/batches', json, (req, res) => {
        const out = createBatch(req.body?.name);
        if (out.error) return res.status(400).json(out);
        res.status(201).json(out.batch);
    });

    router.put('/api/admin/batches-settings', json, (req, res) => reply(res, setReviewAfterDays(Number(req.body?.reviewAfterDays))));

    // every route below works on one existing batch
    router.use('/api/admin/batches/:id', (req, res, next) => {
        req.batch = getBatch(req.params.id);
        if (!req.batch) return res.status(404).json({ error: 'Unknown batch.' });
        next();
    });

    router.patch('/api/admin/batches/:id', json, (req, res) => {
        const out = renameBatch(req.batch.id, req.body?.name);
        if (out.error) return res.status(400).json(out);
        res.json(out.batch);
    });

    router.post('/api/admin/batches/:id/cvs', (req, res, next) => {
        if (req.batch.kind !== 'upload') return res.status(400).json({ error: 'CVs are uploaded into a batch you created. Make a new batch first.' });
        next();
    }, upload.single('cv'), async (req, res, next) => {
        const cv = req.file;
        if (!cv) return res.status(400).json({ error: 'Choose a CV file (PDF, DOCX or TXT).' });
        const file = cv.originalname;
        try {
            const out = await submitApplication({
                cvPath: cv.path, cvName: cv.originalname, name: nameFromFile(cv.originalname), nameFromCv: true,
                practical: null, source: 'batch', batchId: req.batch.id,
            });
            if (out.applicant && !out.restored && !out.moved && !out.added) {
                return res.status(201).json({ file, status: 'added', id: out.applicant.candidateId, name: out.applicant.name,
                    warning: out.result.status === 'warning' ? out.result.codes : undefined });
            }
            fs.rmSync(cv.path, { force: true });             // nothing new stored: keep no copy
            if (out.restored) return res.json({ file, status: 'restored', id: out.applicant.candidateId, name: out.applicant.name });
            if (out.moved) return res.json({ file, status: 'moved', from: out.from, id: out.applicant.candidateId, name: out.applicant.name });
            if (out.duplicate || out.added) return res.json({ file, status: 'duplicate' });
            return res.status(422).json({ file, status: 'failed', error: out.result?.codes?.includes('PASSWORD_PROTECTED')
                ? 'Password-protected PDF.' : 'Could not be read (scanned or empty file?).' });
        } catch (err) {
            fs.rmSync(cv.path, { force: true });
            next(err);
        }
    });

    router.post('/api/admin/batches/:id/archive', (req, res) => reply(res, archiveBatch(req.batch.id)));
    router.post('/api/admin/batches/:id/restore', (req, res) => reply(res, restoreBatch(req.batch.id)));
    router.delete('/api/admin/batches/:id', async (req, res) => reply(res, await deleteBatch(req.batch.id)));
    router.post('/api/admin/batches/:id/review', json, async (req, res) => reply(res, await answerReview(req.batch.id, req.body?.action)));

    router.post('/api/admin/cvs/archive', json, (req, res) => reply(res, archiveCvs(ids(req.body))));
    router.post('/api/admin/cvs/restore', json, (req, res) => reply(res, restoreCvs(ids(req.body))));
    router.post('/api/admin/cvs/delete', json, async (req, res) => reply(res, await deleteCvs(ids(req.body))));

    // upload problems (too big, wrong type) come back as JSON, not an HTML error page
    router.use((err, req, res, next) => {
        if (!/^\/api\/admin\/(batches|cvs)/.test(req.originalUrl)) return next(err);
        if (err instanceof multer.MulterError || err.message?.startsWith('Only PDF')) {
            return res.status(400).json({ error: err.code === 'LIMIT_FILE_SIZE' ? 'File is too large (max 5 MB).' : err.message });
        }
        console.error(err);
        res.status(500).json({ error: 'Something went wrong. Please try again.' });
    });

    return router;
}
