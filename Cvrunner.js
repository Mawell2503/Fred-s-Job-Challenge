// Runs the Python CV reader on ONE file and returns what it found.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve('.');
const PYTHON = process.env.PYTHON_BIN || (process.platform === 'win32' ? 'python' : 'python3');
export const RUNS_DIR = path.resolve('data', 'runs');
const TIMEOUT_MS = 180_000;

/**
 * @param {string} filePath  absolute path of the saved upload
 * @param {number} number    candidate number (1 -> C0001)
 * @returns {Promise<{candidateId, status, codes, text, runDir}>}
 */
export function runCvReader(filePath, number) {
    const candidateId = `C${String(number).padStart(4, '0')}`;
    const runDir = path.join(RUNS_DIR, candidateId);
    fs.mkdirSync(runDir, { recursive: true });

    // execFile (no shell) => the file name can never be interpreted as a command
    const args = ['-m', 'cv_reader', filePath, '-o', runDir, '--id-start', String(number)];

    return new Promise((resolve, reject) => {
        execFile(PYTHON, args, { cwd: ROOT, timeout: TIMEOUT_MS, maxBuffer: 5 * 1024 * 1024 }, (err, _out, stderr) => {
            // exit code 1 > "this CV failed"
            // Anything else is a real error.
            if (err && err.code !== 1) {
                return reject(new Error(`cv_reader could not run: ${err.code ?? err.signal} ${String(stderr).slice(0, 500)}`));
            }
            try {
                const summary = JSON.parse(fs.readFileSync(path.join(runDir, 'batch_summary.json'), 'utf8'));
                const f = summary.files[0];
                const textFile = path.join(runDir, 'cv_text', `${candidateId}.txt`);
                resolve({
                    candidateId,
                    status: f.status,                                   // ok | warning | failed
                    codes: f.messages.map(m => m.code),                 // e.g. NAME_NOT_FOUND, OCR_USED
                    text: fs.existsSync(textFile) ? fs.readFileSync(textFile, 'utf8') : null,
                    runDir,
                });
            } catch (e) {
                reject(new Error(`cv_reader produced no readable summary: ${e.message}`));
            }
        });
    });
}