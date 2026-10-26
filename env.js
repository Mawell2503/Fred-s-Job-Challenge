// Loads KEY=VALUE lines from .env (if present) into process.env, without overriding
// variables that are already set. Imported first by index.js; no extra package needed.
import fs from 'node:fs';

try {
    for (const line of fs.readFileSync('.env', 'utf8').split(/\r?\n/)) {
        const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
        if (!m || line.trimStart().startsWith('#')) continue;
        const value = m[2].replace(/^(['"])(.*)\1$/, '$2');
        if (process.env[m[1]] === undefined) process.env[m[1]] = value;
    }
} catch { /* no .env file: defaults apply */ }
