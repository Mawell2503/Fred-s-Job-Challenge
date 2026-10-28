// Loads KEY=VALUE lines from .env (if present) into process.env, without overriding
// variables that are already set. Imported first by index.js; no extra package needed.
//   KEY=value            -> value
//   KEY=value   # note   -> value      (a " #" after an unquoted value starts a comment)
//   KEY="a # b"          -> a # b      (quotes keep everything inside)
import fs from 'node:fs';

export function parseEnv(text) {
    const out = {};
    for (const line of text.split(/\r?\n/)) {
        if (line.trimStart().startsWith('#')) continue;
        const m = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
        if (!m) continue;
        let value = m[2];
        const quoted = value.match(/^(['"])(.*)\1(\s+#.*)?$/);
        if (quoted) value = quoted[2];
        else value = value.replace(/(^|\s+)#.*$/, '').trim();
        out[m[1]] = value;
    }
    return out;
}

try {
    for (const [key, value] of Object.entries(parseEnv(fs.readFileSync('.env', 'utf8')))) {
        if (process.env[key] === undefined) process.env[key] = value;
    }
} catch { /* no .env file: defaults apply */ }
