// Usage: npm run import -- <folder> [options]
//
// Puts every CV in <folder> (and its sub-folders) into the CV pool, ONE BY ONE, through the same code as
// the application form: Oscar reads each CV, it gets a candidate number, and it shows up everywhere an
// application does (lobby, pipeline page, committee). For testing with a large set of real CVs:
//
//   name       the one Oscar finds in the CV, else "Test subject #0045" (the candidate number)
//   practical  invented, but realistic for a "home" job market (default Mauritius / EUR / French + English):
//              most fit a typical role there; some ask too much, start late, miss a language, live abroad
//              without the right to work there... so Nina has real things to flag. Each file always gets the
//              same answers (they are drawn from the file's fingerprint), so a re-import is reproducible.
//   linkedin   a made-up LinkedIn URL for about 45% of them (no LinkedIn PDF)
//   applied    about half are open applications, the rest applied for one of your existing roles
//   applied at spread over the last 60 days
//   batch      all of them go into one batch: "Import · <folder> · <date>", or the name given with --batch
//              (running the same command again fills the same batch)
//
// Safe to stop (Ctrl+C finishes the current CV, then stops) and to run again: a CV already in the pool
// is skipped. Unreadable files are listed at the end and in data/import-<date>.log.
// STOP THE SERVER FIRST: both write data/applicants.json.
//
// Options:
//   --open 0.5                share of open applications (0 to 1)
//   --country Mauritius --city "Port Louis" --currency EUR --languages French,English
//                             the "home" job market the invented answers are built around
//   --limit 100               only the first 100 files (a quick try)
//   --seed anything           other invented answers for the same files
//   --batch "Client X"        the batch they go into (made if it doesn't exist)
import '../env.js';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { listRoles } from '../rolesStore.js';
import { batchNamed } from '../batches.js';
import { cleanPractical } from '../practical.js';
import { DATA_DIR, UPLOAD_DIR, fileHash, listApplicants, saveApplicants, submitApplication } from '../applicants.js';

const MAX_BYTES = 5 * 1024 * 1024;
const TYPES = new Set(['.pdf', '.docx', '.txt']);

// ---------------- arguments ----------------
const argv = process.argv.slice(2);
const opt = (name, fallback) => {
    const i = argv.indexOf(`--${name}`);
    return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : fallback;
};
const folder = argv.find((a, i) => !a.startsWith('--') && !argv[i - 1]?.startsWith('--'));
if (!folder || !fs.existsSync(folder) || !fs.statSync(folder).isDirectory()) {
    console.log('Usage: npm run import -- <folder with CVs> [--open 0.5] [--country Mauritius] [--city "Port Louis"]\n' +
        '                        [--currency EUR] [--languages French,English] [--limit 100] [--seed text]\n' +
        (folder ? `\nNot a folder: ${folder}` : ''));
    process.exit(1);
}
const HOME = {
    country: opt('country', 'Mauritius'),
    city: opt('city', opt('country') ? '' : 'Port Louis'),
    currency: String(opt('currency', 'EUR')).toUpperCase(),
    languages: String(opt('languages', 'French,English')).split(',').map(s => s.trim()).filter(Boolean),
};
const OPEN_SHARE = Math.min(1, Math.max(0, Number(opt('open', 0.5))));
const LIMIT = Number(opt('limit', 0)) || Infinity;
const SEED = String(opt('seed', ''));
const BATCH_NAME = String(opt('batch', `Import · ${path.basename(path.resolve(folder))} · ${new Date().toISOString().slice(0, 10)}`));

// ---------------- the server must not be running ----------------
try {
    const port = process.env.PORT || 3000;
    await fetch(`http://localhost:${port}/api/roles`, { signal: AbortSignal.timeout(1500) });
    console.log(`The server is running on port ${port}. Stop it first (Ctrl+C in its window): both write data/applicants.json.`);
    process.exit(1);
} catch { /* not running: good */ }

// ---------------- invented answers, the same for the same file ----------------
function rngFrom(text) {
    let a = crypto.createHash('sha256').update(text).digest().readUInt32LE(0);
    return () => {                                          // mulberry32
        a = (a + 0x6D2B79F5) | 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}
function picker(rng) {
    const one = list => list[Math.floor(rng() * list.length)];
    const weighted = pairs => {                             // [[value, weight], ...]
        let r = rng() * pairs.reduce((s, [, w]) => s + w, 0);
        for (const [v, w] of pairs) if ((r -= w) < 0) return v;
        return pairs.at(-1)[0];
    };
    return { one, weighted, between: (lo, hi) => lo + rng() * (hi - lo), chance: p => rng() < p };
}

// rough monthly salary per EUR, only to invent believable amounts (Nina never converts)
const PER_EUR = { EUR: 1, USD: 1.1, GBP: 0.85, MUR: 50, INR: 92, ZAR: 20, CHF: 0.95, CAD: 1.5, AUD: 1.65, AED: 4, SGD: 1.45, MGA: 4900, KES: 140, NGN: 1700 };
const ABROAD = [
    { country: 'France', city: 'Lyon', currency: 'EUR', native: 'French' },
    { country: 'India', city: 'Bangalore', currency: 'INR', native: 'Hindi', also: 'English' },
    { country: 'South Africa', city: 'Cape Town', currency: 'ZAR', native: 'English' },
    { country: 'United Kingdom', city: 'London', currency: 'GBP', native: 'English' },
    { country: 'Madagascar', city: 'Antananarivo', currency: 'MGA', native: 'Malagasy', also: 'French' },
    { country: 'Kenya', city: 'Nairobi', currency: 'KES', native: 'Swahili', also: 'English' },
    { country: 'Germany', city: 'Berlin', currency: 'EUR', native: 'German' },
    { country: 'Belgium', city: 'Brussels', currency: 'EUR', native: 'French' },
];
const EXTRA_LANGS = ['Spanish', 'German', 'Italian', 'Portuguese', 'Mandarin', 'Hindi', 'Arabic', 'Creole'];
const LEVEL = [['native', 25], ['C2', 15], ['C1', 30], ['B2', 17], ['B1', 9], ['A2', 4]];

function inventPractical(rng) {
    const p = picker(rng);
    const atHome = p.chance(0.72);
    const from = atHome ? null : p.one(ABROAD);
    const country = atHome ? HOME.country : from.country;
    const city = atHome ? (p.chance(0.5) ? HOME.city : '') : (p.chance(0.7) ? from.city : '');
    const localCur = atHome ? HOME.currency : from.currency;
    const currency = p.chance(atHome ? 0.9 : 0.7) ? localCur : p.one([HOME.currency, 'EUR', 'USD']);
    const monthlyEur = p.chance(0.15) ? p.between(6000, 9000) : p.between(2800, 6000);
    const yearly = p.chance(0.15);
    const raw = monthlyEur * (PER_EUR[currency] || 1) * (yearly ? 12 : 1);
    const step = raw > 100000 ? 1000 : 100;
    const days = p.chance(0.1) ? p.between(150, 240) : p.between(0, 150) ** 1.15 / 150 ** 0.15;
    const start = new Date(Date.now() + days * 864e5).toISOString().slice(0, 10);

    const langs = [];
    const add = (language, level) => { if (!langs.some(l => l.language.toLowerCase() === language.toLowerCase())) langs.push({ language, level }); };
    if (from) add(from.native, 'native');
    if (from?.also) add(from.also, p.weighted(LEVEL.slice(1)));
    for (const l of HOME.languages) if (p.chance(atHome ? 0.85 : 0.55)) add(l, p.weighted(atHome ? LEVEL : LEVEL.slice(1)));
    if (p.chance(0.25)) add(p.one(EXTRA_LANGS), p.weighted(LEVEL.slice(2)));
    if (!langs.length) add(HOME.languages[0] || 'English', p.weighted(LEVEL));

    const work = atHome ? [HOME.country] : (p.chance(0.75) ? [from.country] : [from.country, HOME.country]);
    if (atHome && p.chance(0.1) && HOME.country !== 'France') work.push('France');

    return {
        salary: { amount: Math.max(step, Math.round(raw / step) * step), period: yearly ? 'year' : 'month', currency },
        available_from: start,
        notice_weeks: p.chance(0.3) ? null : p.one([0, 2, 4, 4, 4, 8, 12]),
        location: { country, city },
        relocate: atHome ? p.weighted([['yes', 30], ['open', 40], ['no', 30]]) : p.weighted([['yes', 50], ['open', 35], ['no', 15]]),
        travel: p.weighted([['none', 20], ['occasional', 65], ['frequent', 15]]),
        arrangement: p.weighted([['onsite', 25], ['hybrid', 55], ['remote', 20]]),
        work_countries: work,
        languages: langs,
    };
}

const slug = name => name.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'profile';

// ---------------- the files ----------------
function walk(dir, out = []) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        if (e.name.startsWith('.')) continue;
        const full = path.join(dir, e.name);
        if (e.isDirectory()) walk(full, out);
        else if (e.isFile()) out.push(full);
    }
    return out;
}
const all = walk(path.resolve(folder));
const skipped = [];
const files = [];
for (const f of all) {
    const ext = path.extname(f).toLowerCase();
    const size = fs.statSync(f).size;
    if (!TYPES.has(ext)) skipped.push([f, 'not a PDF, DOCX or TXT']);
    else if (size === 0) skipped.push([f, 'empty file']);
    else if (size > MAX_BYTES) skipped.push([f, 'larger than 5 MB (the form refuses those too)']);
    else files.push(f);
}
const todo = files.slice(0, LIMIT);
const batch = todo.length ? batchNamed(BATCH_NAME) : null;
const roles = listRoles();
console.log(`${files.length} CV file(s) found${skipped.length ? `, ${skipped.length} other file(s) skipped` : ''}` +
    `${todo.length < files.length ? `; importing the first ${todo.length}` : ''}.`);
console.log(roles.length ? `Applied for: ${Math.round(OPEN_SHARE * 100)}% open applications, the rest spread over ${roles.map(r => r.id).join(', ')}.`
    : 'No roles yet: every CV is an open application.');
if (batch) console.log(`Batch: ${batch.name} (${batch.id}).`);
console.log(`Home market for the invented answers: ${HOME.country}${HOME.city ? ` (${HOME.city})` : ''}, ${HOME.currency}, ${HOME.languages.join(' + ')}.\n`);

let stop = false;
process.on('SIGINT', () => {
    if (stop) process.exit(130);
    stop = true;
    console.log('\nStopping after this CV (Ctrl+C again to quit at once)...');
});

const counts = { added: 0, already: 0, unreadable: 0, failed: 0 };
const problems = [];
const width = String(todo.length).length;
const started = Date.now();
for (const [i, file] of todo.entries()) {
    if (stop) break;
    const ext = path.extname(file).toLowerCase();
    const copy = path.join(UPLOAD_DIR, crypto.randomUUID() + ext);
    const tag = `[${String(i + 1).padStart(width)}/${todo.length}]`;
    let kept = false;
    try {
        fs.copyFileSync(file, copy);
        const sha = fileHash(copy);
        // already in the pool (an earlier import, the form, or the same file twice in this folder): leave that
        // person exactly as they are, even when another --seed would now pick another role for them
        if (listApplicants().some(a => a.cvSha256 === sha)) {
            counts.already++;
            console.log(`${tag} already in the pool, skipped   ${path.basename(file)}`);
            continue;
        }
        const rng = rngFrom(sha + SEED);
        const p = picker(rng);
        const role = roles.length && !p.chance(OPEN_SHARE) ? p.one(roles) : null;
        const practical = cleanPractical(inventPractical(rng), role);
        if (practical.error) throw new Error(`invented answers refused: ${practical.error}`);
        const appliedAt = new Date(Date.now() - p.between(0, 60) * 864e5).toISOString();
        const wantsLinkedin = p.chance(0.45), liTail = crypto.createHash('sha1').update(file).digest('hex').slice(0, 4);

        const out = await submitApplication({ roleId: role?.id || null, cvPath: copy, cvName: path.basename(file),
            name: '', practical: practical.value, source: 'import', nameFromCv: true, appliedAt, batchId: batch.id });
        if (out.duplicate || out.added || out.restored || out.moved) {
            counts.already++;
            console.log(`${tag} already in the pool, skipped   ${path.basename(file)}`);
            continue;
        }
        if (!out.applicant) {
            counts.unreadable++;
            problems.push([file, 'Oscar could not read it (scanned image without OCR, protected or broken file?)']);
            console.log(`${tag} UNREADABLE                     ${path.basename(file)}`);
            continue;
        }
        kept = true;
        const a = out.applicant;
        if (!a.name) a.name = `Test subject #${String(a.number).padStart(4, '0')}`;
        if (wantsLinkedin) a.linkedinUrl = `https://www.linkedin.com/in/${slug(a.name)}-${liTail}`;
        saveApplicants();
        counts.added++;
        const eta = (Date.now() - started) / (i + 1) * (todo.length - i - 1) / 60000;
        console.log(`${tag} ${a.candidateId}  ${a.name.slice(0, 28).padEnd(28)} ${out.result.status.padEnd(7)} ` +
            `${role ? `applied for ${role.id}` : 'open application'}${i % 25 === 24 ? `   (~${Math.ceil(eta)} min left)` : ''}`);
    } catch (err) {
        counts.failed++;
        problems.push([file, err.message]);
        console.log(`${tag} ERROR ${err.message}   ${path.basename(file)}`);
    } finally {
        if (!kept) fs.rmSync(copy, { force: true });        // nothing recorded: keep no copy
    }
}

const mins = ((Date.now() - started) / 60000).toFixed(1);
console.log(`\nDone in ${mins} min: ${counts.added} added, ${counts.already} already in the pool, ` +
    `${counts.unreadable} unreadable, ${counts.failed} error(s)${stop ? ' (stopped early: run the same command again to continue)' : ''}.`);
if (problems.length || skipped.length) {
    const log = path.join(DATA_DIR, `import-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.log`);
    fs.writeFileSync(log, [...problems.map(([f, why]) => `NOT IMPORTED  ${f}\n              ${why}`),
        ...skipped.map(([f, why]) => `SKIPPED       ${f}\n              ${why}`)].join('\n') + '\n');
    console.log(`Files not imported: ${problems.length + skipped.length}. The list is in ${log}`);
}
console.log('Next: start the server, choose a role in the lobby and run it on the whole CV pool.');
