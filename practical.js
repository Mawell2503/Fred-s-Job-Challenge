// Practical details for NINA (the operations member of the committee).
//
//   Applicant side: salary expectation (in a currency they choose), availability, location, mobility,
//     the countries they can work in, languages ... filled in on index.html, REQUIRED, stored on the
//     applicant record (`practical`). The answers belong to the person, not to a role: the same answers
//     are checked against whichever role the CV pool is run for.
//   Role side: budget, client country, required languages, start date ... ("logistics"),
//     set by the admin on roles.html.
//
// None of this goes through the funnel (Oscar / Remi / Jules). After Jules has picked the
// top 10, the practical answers of those 10 + the role's logistics go straight to the
// committee (see pipeline.js). No express import here, so it can be tested on its own.

export const LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2', 'native'];
export const RELOCATE = ['yes', 'no', 'open'];
export const TRAVEL = ['none', 'occasional', 'frequent'];
export const ARRANGEMENT = ['onsite', 'hybrid', 'remote'];
export const WORK_AUTH = ['yes', 'no', 'sponsorship'];          // older records only: see work_countries
// the currencies offered on the form (any other 3-letter ISO code is accepted too)
export const CURRENCIES = ['EUR', 'USD', 'GBP', 'MUR', 'INR', 'ZAR', 'CHF', 'CAD', 'AUD', 'AED', 'SGD', 'MGA', 'KES', 'NGN'];
export const PERIODS = ['month', 'year'];

const str = (v, max) => String(v ?? '').trim().slice(0, max);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
// a real calendar day: "2026-02-31" parses (JavaScript rolls it over to March 3) but is not one
const isDate = v => {
    if (!DATE_RE.test(v)) return false;
    const d = new Date(v + 'T00:00:00Z');
    return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v;
};
const num = v => (v === '' || v === null || v === undefined ? NaN : Number(v));
const isCurrency = v => /^[A-Z]{3}$/.test(String(v ?? '').trim().toUpperCase());

/** "Mauritius, France" or ["Mauritius", "France"] -> ["Mauritius", "France"] (trimmed, no duplicates, max 10). */
export function cleanCountries(v) {
    const list = Array.isArray(v) ? v : String(v ?? '').split(/[,;\n]/);
    const out = [], seen = new Set();
    for (const c of list) {
        const name = str(c, 60);
        if (!name || seen.has(name.toLowerCase())) continue;
        seen.add(name.toLowerCase());
        out.push(name);
        if (out.length >= 10) break;
    }
    return out;
}

// `lenient` (the role's requirements, which never fail): an incomplete row is dropped on its own,
// the other languages are kept. Otherwise (the applicant's answers) it is an error they must fix.
function cleanLanguages(list, levelKey, { required, lenient = false }) {
    const out = [];
    const seen = new Set();
    for (const item of Array.isArray(list) ? list : []) {
        const language = str(item?.language, 40);
        const level = item?.[levelKey];
        if (!language && !level) continue;                 // an empty row the user left
        if (lenient && (!language || !LEVELS.includes(level))) continue;
        if (!language) return { error: 'Name each language you list.' };
        if (!LEVELS.includes(level)) return { error: `Choose a level for ${language}.` };
        const key = language.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ language, [levelKey]: level });
        if (out.length >= 8) break;
    }
    if (required && !out.length) return { error: 'Add at least one language you speak, with your level.' };
    return { value: out };
}

/**
 * Validate what the applicant sent. `raw` is the parsed `practical` JSON from the form.
 * Returns { value } or { error } (the error is shown to the applicant as is).
 */
export function cleanPractical(raw, role) {
    let p = raw;
    if (typeof p === 'string') {
        try { p = JSON.parse(p); } catch { return { error: 'Fill in the practical details.' }; }
    }
    if (!p || typeof p !== 'object') return { error: 'Fill in the practical details.' };

    const amount = num(p.salary?.amount);
    if (!(amount > 0 && amount < 1e9)) return { error: 'Enter your salary expectation (a number).' };
    const period = PERIODS.includes(p.salary?.period) ? p.salary.period : null;
    if (!period) return { error: 'Say whether your salary expectation is per month or per year.' };
    const currency = isCurrency(p.salary?.currency) ? String(p.salary.currency).trim().toUpperCase() : roleCurrency(role);

    const available = str(p.available_from, 10);
    if (!isDate(available)) return { error: 'Enter the earliest date you could start.' };
    const noticeRaw = num(p.notice_weeks);
    const notice_weeks = Number.isFinite(noticeRaw) && noticeRaw >= 0 && noticeRaw <= 52 ? Math.round(noticeRaw) : null;

    const country = str(p.location?.country, 60);
    if (!country) return { error: 'Enter the country you live in.' };
    const city = str(p.location?.city, 60);

    const pick = (v, list, msg) => (list.includes(v) ? { value: v } : { error: msg });
    const relocate = pick(p.relocate, RELOCATE, 'Say whether you would relocate.');
    const travel = pick(p.travel, TRAVEL, 'Say how much you can travel.');
    const arrangement = pick(p.arrangement, ARRANGEMENT, 'Choose your preferred work arrangement.');
    for (const r of [relocate, travel, arrangement]) if (r.error) return r;
    const work_countries = cleanCountries(p.work_countries);
    if (!work_countries.length) return { error: 'List the countries you can work in without a visa (at least one).' };

    const langs = cleanLanguages(p.languages, 'level', { required: true });
    if (langs.error) return langs;

    return {
        value: {
            salary: { amount: Math.round(amount), period, currency },
            available_from: available,
            notice_weeks,
            location: { country, city },
            relocate: relocate.value,
            travel: travel.value,
            arrangement: arrangement.value,
            work_countries,
            languages: langs.value,
        },
    };
}

/** What the admin sets on the role for Nina. Never fails: bad values are dropped. */
export function cleanLogistics(raw) {
    const l = raw && typeof raw === 'object' ? raw : {};
    const money = v => { const n = num(v); return Number.isFinite(n) && n >= 0 && n < 1e9 ? Math.round(n) : null; };
    let budget_min = money(l.budget_min), budget_max = money(l.budget_max);
    if (budget_min !== null && budget_max !== null && budget_min > budget_max) [budget_min, budget_max] = [budget_max, budget_min];
    const currency = /^[A-Za-z]{3}$/.test(String(l.currency ?? '').trim()) ? String(l.currency).trim().toUpperCase() : 'EUR';
    const start = str(l.start_date, 10);
    return {
        currency,
        budget_min,
        budget_max,
        budget_period: PERIODS.includes(l.budget_period) ? l.budget_period : 'month',
        client_country: str(l.client_country, 60),
        work_country: str(l.work_country, 60),
        work_city: str(l.work_city, 60),
        arrangement: ARRANGEMENT.includes(l.arrangement) ? l.arrangement : '',
        travel: TRAVEL.includes(l.travel) ? l.travel : '',
        start_date: isDate(start) ? start : '',
        languages: cleanLanguages(l.languages, 'min_level', { required: false, lenient: true }).value,
        notes: str(l.notes, 1000),
    };
}

/** True when the admin has set at least one requirement Nina can check. */
export function hasLogistics(l) {
    if (!l) return false;
    return Boolean(l.budget_min !== null && l.budget_min !== undefined || l.budget_max !== null && l.budget_max !== undefined
        || l.client_country || l.work_country || l.arrangement || l.travel || l.start_date
        || (l.languages && l.languages.length) || l.notes);
}

export const roleCurrency = role => role?.logistics?.currency || 'EUR';

/**
 * Answers saved before the CV pool existed said "right to work in the country of this position"
 * (yes / no / sponsorship) for the one role they applied to. Turn that into the countries they can
 * work in: "yes" -> that role's work country (when it had one). The old answer is kept as it was.
 */
export function upgradePractical(p, role) {
    if (!p || Array.isArray(p.work_countries)) return false;
    const wc = role?.logistics?.work_country;
    p.work_countries = p.work_authorization === 'yes' && wc ? [wc] : [];
    return true;
}

/** The little the public application form may know about a role (never the budget). */
export const publicRoleInfo = role => ({
    id: role.id,
    title: role.title,
    currency: roleCurrency(role),
    work_country: role.logistics?.work_country || '',
});
