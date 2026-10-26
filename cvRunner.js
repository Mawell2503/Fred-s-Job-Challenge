// Runs the Python CV reader (OSCAR) on ONE file and returns what it found.
import fs from 'node:fs';
import path from 'node:path';
import { runPython } from './pyRunner.js';

export const RUNS_DIR = path.resolve('data', 'runs');
const TIMEOUT_MS = 180_000;

/**
 * @param {string} filePath  absolute path of the saved upload
 * @param {number} number    candidate number (1 -> C0001)
 * @param {object} [rules]   the role's keep/drop settings, e.g. { sections: { interests: 'drop' } }
 * @returns {Promise<{candidateId, status, codes, text, runDir}>}
 */
export async function runCvReader(filePath, number, rules) {
    const candidateId = `C${String(number).padStart(4, '0')}`;
    const runDir = path.join(RUNS_DIR, candidateId);
    fs.mkdirSync(runDir, { recursive: true });

    const args = ['-m', 'cv_reader', filePath, '-o', runDir, '--id-start', String(number)];
    if (rules && Object.keys(rules.sections || {}).length) {
        const rulesFile = path.join(runDir, 'role_rules.json');
        fs.writeFileSync(rulesFile, JSON.stringify(rules));
        args.push('--rules', rulesFile);
    }

    const { code, stderr } = await runPython(args, TIMEOUT_MS);
    // exit code 1 > "this CV failed"; anything else non-zero is a real error.
    if (code !== 0 && code !== 1) {
        throw new Error(`cv_reader could not run (exit ${code}): ${stderr.slice(0, 500)}`);
    }
    try {
        const summary = JSON.parse(fs.readFileSync(path.join(runDir, 'batch_summary.json'), 'utf8'));
        const f = summary.files[0];
        const textFile = path.join(runDir, 'cv_text', `${candidateId}.txt`);
        return {
            candidateId,
            status: f.status,                                   // ok | warning | failed
            codes: f.messages.map(m => m.code),                 // e.g. NAME_NOT_FOUND, OCR_USED
            text: fs.existsSync(textFile) ? fs.readFileSync(textFile, 'utf8') : null,
            runDir,
        };
    } catch (e) {
        throw new Error(`cv_reader produced no readable summary: ${e.message}`);
    }
}
