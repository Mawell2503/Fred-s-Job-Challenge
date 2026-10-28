// Job roles (one per position being hired). Stored in data/roles.json; each role's agent
// results live in data/roles/<id>/ (job.json, remi.json, jules.json, progress.json).
import fs from 'node:fs';
import path from 'node:path';

const DATA_DIR = path.resolve('data');
const ROLES_FILE = path.join(DATA_DIR, 'roles.json');
export const SECTION_TYPES = ['summary', 'experience', 'education', 'skills', 'languages', 'certifications',
    'training', 'projects', 'awards', 'publications', 'volunteering', 'interests', 'references',
    'personal', 'declaration', 'unknown'];
export const CRITERION_TYPES = ['skill', 'language', 'years', 'degree', 'certification', 'other'];
export const AGENTS = ['clara', 'oscar', 'remi', 'jules'];

fs.mkdirSync(DATA_DIR, { recursive: true });
let roles = [];
try { roles = JSON.parse(fs.readFileSync(ROLES_FILE, 'utf8')); } catch { /* first run */ }

export function saveRoles() {
    const tmp = `${ROLES_FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(roles, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, ROLES_FILE);
}

// A job that was running when the server stopped will never finish: say so, on disk too.
{
    let changed = false;
    for (const r of roles) {
        for (const a of Object.values(r.agents || {})) {
            if (a.state !== 'running') continue;
            Object.assign(a, { state: 'failed', message: 'The server restarted while this was running.',
                finishedAt: new Date().toISOString() });
            changed = true;
        }
    }
    if (changed) saveRoles();
}

export const roleDir = id => {
    const dir = path.join(DATA_DIR, 'roles', id);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
};

export const listRoles = () => roles;
export const getRole = id => roles.find(r => r.id === id) || null;

export function createRole(title) {
    const n = roles.reduce((m, r) => Math.max(m, Number(r.id.slice(1)) || 0), 0) + 1;
    const role = {
        id: `R${String(n).padStart(3, '0')}`,
        title,
        status: 'open',
        createdAt: new Date().toISOString(),
        adText: '', mustText: '', niceText: '',
        job: { title, summary: '', must_haves: [], nice_to_haves: [], suggested_drop_sections: [] },
        jobUpdatedAt: null,
        claraProposal: null,
        // Oscar keep/drop per section for this role (anything not listed keeps the default)
        rules: { sections: { interests: 'drop', references: 'drop', declaration: 'drop' } },
        agents: {},
    };
    roles.push(role);
    saveRoles();
    return role;
}

// ---------- validation of what the admin page sends ----------
const str = (v, max) => String(v ?? '').trim().slice(0, max);

export function cleanCriterion(c) {
    if (!c || typeof c !== 'object') return null;
    const text = str(c.text, 300);
    const type = CRITERION_TYPES.includes(c.type) ? c.type : 'other';
    const keywords = (Array.isArray(c.keywords) ? c.keywords : String(c.keywords ?? '').split(','))
        .map(k => str(k, 80)).filter(Boolean).slice(0, 10);
    if (!text && !keywords.length) return null;
    const out = { text: text || keywords.join(', '), type, keywords };
    if (type === 'years') {
        const y = Number(c.min_years);
        out.min_years = Number.isFinite(y) && y >= 0 && y <= 60 ? y : 0;
    }
    return out;
}

export function cleanJob(job, title) {
    const list = v => (Array.isArray(v) ? v : []).map(cleanCriterion).filter(Boolean).slice(0, 30);
    return {
        title,
        summary: str(job?.summary, 2000),
        must_haves: list(job?.must_haves),
        nice_to_haves: list(job?.nice_to_haves),
        suggested_drop_sections: (Array.isArray(job?.suggested_drop_sections) ? job.suggested_drop_sections : [])
            .filter(s => SECTION_TYPES.includes(s)),
    };
}

export function cleanRules(rules) {
    const sections = {};
    for (const [k, v] of Object.entries(rules?.sections || {})) {
        if (SECTION_TYPES.includes(k) && (v === 'keep' || v === 'drop')) sections[k] = v;
    }
    return { sections };
}
