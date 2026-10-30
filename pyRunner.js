// Runs a Python module (cv_reader or hiring) without a shell and collects its output.
import { execFile } from 'node:child_process';
import path from 'node:path';

export const ROOT = path.resolve('.');
export const PYTHON = process.env.PYTHON_BIN || (process.platform === 'win32' ? 'python' : 'python3');

/**
 * @param {string[]} args     e.g. ['-m', 'hiring', 'remi', ...]
 * @param {number} timeoutMs
 * @param {AbortSignal} [signal]  aborting it kills the process (SIGTERM: the hiring CLI then kills its own
 *                                claude processes too); the promise then rejects with the signal's reason
 * @returns {Promise<{code:number, stdout:string, stderr:string}>}  never rejects for a non-zero exit
 */
export function runPython(args, timeoutMs, signal) {
    return new Promise((resolve, reject) => {
        if (signal?.aborted) return reject(signal.reason);
        // execFile (no shell) => file names and user text can never run as commands
        execFile(PYTHON, args, { cwd: ROOT, timeout: timeoutMs, maxBuffer: 20 * 1024 * 1024, signal },
            (err, stdout, stderr) => {
                if (signal?.aborted) return reject(signal.reason);
                if (err && typeof err.code !== 'number') {
                    // could not start, or killed by the timeout
                    return reject(new Error(`python could not run: ${err.code ?? err.signal} ${String(stderr).slice(0, 500)}`));
                }
                resolve({ code: err ? err.code : 0, stdout: String(stdout), stderr: String(stderr) });
            });
    });
}
