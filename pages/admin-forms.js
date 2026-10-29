// The admin's editing forms, shared by the pipeline page (roles.html) and the lobby's timeline cards
// (adminlobby.html), so both always edit a role the same way:
//   criteriaEditor  - Clara's must-haves / nice-to-haves (+ the role summary), the `job` field of a role
//   sectionsEditor  - Oscar's CV sections to keep / drop, the `rules` field of a role
//   logisticsEditor - the practical requirements Nina checks, the `logistics` field of a role
// Each editor only keeps its own copy of the data and builds its own rows; the page decides when to save
// (PATCH /api/admin/roles/:id). No innerHTML anywhere: criteria text comes from the AI and is shown as text.
// Loaded as a plain <script> before the page's own script; everything lives on window.AdminForms.
window.AdminForms = (() => {
    const SECTIONS = ['summary', 'experience', 'education', 'skills', 'languages', 'certifications', 'training',
        'projects', 'awards', 'publications', 'volunteering', 'interests', 'references', 'personal', 'declaration', 'unknown'];
    const TYPES = ['skill', 'language', 'years', 'degree', 'certification', 'other'];
    const LEVELS = [['A1', 'A1'], ['A2', 'A2'], ['B1', 'B1'], ['B2', 'B2'], ['C1', 'C1'], ['C2', 'C2'], ['native', 'Native']];
    const emptyJob = () => ({ summary: '', must_haves: [], nice_to_haves: [], suggested_drop_sections: [] });

    function h(tag, attrs = {}, ...kids) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v === undefined || v === null || v === false) continue;
            if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
            else if (k === 'text') el.textContent = v;
            else el.setAttribute(k, v === true ? '' : v);
        }
        for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
            el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
        return el;
    }
    const removeBtn = onclick => h('button', { class: 'btn x', type: 'button', title: 'Remove', 'aria-label': 'Remove', text: '×', onclick });

    /** Must-haves + nice-to-haves + summary. `summary` is a textarea, the lists are empty containers. */
    function criteriaEditor({ summary, mustList, niceList }) {
        let job = emptyJob();

        function row(list, c, i) {
            const kw = h('input', { value: (c.keywords || []).join(', '), placeholder: 'keywords', 'aria-label': 'Keywords',
                oninput: e => { c.keywords = e.target.value.split(',').map(s => s.trim()).filter(Boolean); } });
            const years = h('input', { type: 'number', min: 0, max: 60, step: 0.5, value: c.min_years ?? '', placeholder: 'years',
                'aria-label': 'Minimum years', disabled: c.type !== 'years', oninput: e => { c.min_years = Number(e.target.value); } });
            const type = h('select', { 'aria-label': 'Type', onchange: e => { c.type = e.target.value; years.disabled = c.type !== 'years'; } },
                ...TYPES.map(t => h('option', { value: t, selected: c.type === t, text: t })));
            return h('div', { class: 'crit' },
                h('input', { value: c.text || '', placeholder: 'e.g. At least 5 years of accounting experience', 'aria-label': 'Criterion',
                    oninput: e => { c.text = e.target.value; } }),
                type, kw, years,
                removeBtn(() => { list.splice(i, 1); render(); }));
        }

        function render() {
            summary.value = job.summary || '';
            mustList.replaceChildren(...job.must_haves.map((c, i) => row(job.must_haves, c, i)));
            niceList.replaceChildren(...job.nice_to_haves.map((c, i) => row(job.nice_to_haves, c, i)));
        }
        summary.addEventListener('input', e => { job.summary = e.target.value; });

        return {
            /** Start editing a copy of `j` (the role's saved job). */
            set(j) { job = { ...emptyJob(), ...structuredClone(j || {}) }; render(); },
            /** What to PATCH as `job`. */
            get: () => job,
            add(kind) { job[kind === 'must' ? 'must_haves' : 'nice_to_haves'].push({ text: '', type: 'skill', keywords: [] }); render(); },
            /** Clara's proposal replaces summary + criteria (the title and everything else stay). */
            loadProposal(p) {
                job = { ...job, summary: p.summary, must_haves: structuredClone(p.must_haves),
                    nice_to_haves: structuredClone(p.nice_to_haves), suggested_drop_sections: p.suggested_drop_sections };
                render();
            },
            count: () => job.must_haves.length + job.nice_to_haves.length,
            suggestedDrop: () => job.suggested_drop_sections || [],
        };
    }

    /** One checkbox per CV section; ticked = keep. */
    function sectionsEditor(container) {
        let rules = { sections: {} };
        function render() {
            const s = rules.sections || (rules.sections = {});
            container.replaceChildren(...SECTIONS.map(t => h('label', {},
                h('input', { type: 'checkbox', checked: s[t] !== 'drop', onchange: e => { s[t] = e.target.checked ? 'keep' : 'drop'; } }), t)));
        }
        return {
            set(r) { rules = structuredClone(r || { sections: {} }); render(); },
            get: () => rules,
            /** Clara's suggested drops; false when she has none. */
            suggest(drop) {
                const d = new Set(drop || []);
                if (!d.size) return false;
                rules.sections = Object.fromEntries(SECTIONS.map(t => [t, d.has(t) ? 'drop' : 'keep']));
                render();
                return true;
            },
        };
    }

    /** The practical requirements form. Its inputs have ids `${prefix}currency`, `${prefix}min` ... (see FIELDS). */
    function logisticsEditor(prefix = 'lg-') {
        const $ = id => document.getElementById(prefix + id);
        const FIELDS = [['currency', 'currency'], ['min', 'budget_min'], ['max', 'budget_max'], ['period', 'budget_period'],
            ['client', 'client_country'], ['start', 'start_date'], ['wcountry', 'work_country'], ['wcity', 'work_city'],
            ['arr', 'arrangement'], ['travel', 'travel'], ['notes', 'notes']];
        let langs = [];

        function langRow(l, i) {
            return h('div', { class: 'lang-row' },
                h('input', { value: l.language || '', placeholder: 'e.g. French', 'aria-label': 'Language',
                    oninput: e => { l.language = e.target.value; } }),
                h('select', { 'aria-label': 'Minimum level', onchange: e => { l.min_level = e.target.value; } },
                    h('option', { value: '', text: 'Minimum level…' }),
                    ...LEVELS.map(([v, t]) => h('option', { value: v, selected: l.min_level === v, text: t }))),
                removeBtn(() => { langs.splice(i, 1); renderLangs(); }));
        }
        const renderLangs = () => $('langs').replaceChildren(...langs.map(langRow));

        return {
            set(l = {}) {
                for (const [id, key] of FIELDS) $(id).value = l[key] ?? '';
                $('currency').value = l.currency || 'EUR';
                $('period').value = l.budget_period || 'month';
                langs = structuredClone(l.languages || []);
                renderLangs();
            },
            /** What to PATCH as `logistics` (the server cleans it). */
            get() {
                const out = Object.fromEntries(FIELDS.map(([id, key]) => [key, $(id).value.trim()]));
                out.languages = langs;
                return out;
            },
            addLang() { langs.push({ language: '', min_level: '' }); renderLangs(); },
        };
    }

    /** True when the role has at least one requirement Nina can check (mirrors hasLogistics in practical.js). */
    function hasLogistics(l) {
        if (!l) return false;
        const set = v => v !== null && v !== undefined && v !== '';
        return Boolean(set(l.budget_min) || set(l.budget_max) || l.client_country || l.work_country || l.arrangement
            || l.travel || l.start_date || l.languages?.length || l.notes);
    }

    return { SECTIONS, TYPES, LEVELS, criteriaEditor, sectionsEditor, logisticsEditor, hasLogistics };
})();
