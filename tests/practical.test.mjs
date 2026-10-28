// node --test tests/   (no dependencies)
import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanPractical, cleanLogistics, hasLogistics, publicRoleInfo } from '../practical.js';

const role = { id: 'R001', title: 'Data engineer', logistics: cleanLogistics({ currency: 'eur', work_country: 'France', budget_max: 5000 }) };
const good = {
    salary: { amount: '4500', period: 'month' },
    available_from: '2026-12-01',
    notice_weeks: '4',
    location: { country: 'Mauritius', city: 'Port Louis' },
    relocate: 'open', travel: 'occasional', arrangement: 'hybrid', work_authorization: 'sponsorship',
    languages: [{ language: 'French', level: 'C1' }, { language: 'french', level: 'B1' }, { language: '', level: '' }],
};

test('a complete answer is accepted and normalised', () => {
    const { value, error } = cleanPractical(JSON.stringify(good), role);
    assert.equal(error, undefined);
    assert.deepEqual(value.salary, { amount: 4500, period: 'month', currency: 'EUR' });
    assert.equal(value.notice_weeks, 4);
    assert.deepEqual(value.languages, [{ language: 'French', level: 'C1' }]);   // duplicate + empty row dropped
});

test('every required part is enforced with a readable message', () => {
    const without = (path, v) => {
        const p = structuredClone(good);
        const keys = path.split('.');
        let o = p;
        for (const k of keys.slice(0, -1)) o = o[k];
        o[keys.at(-1)] = v;
        return cleanPractical(p, role).error;
    };
    assert.match(without('salary.amount', ''), /salary/);
    assert.match(without('salary.period', 'week'), /per month or per year/);
    assert.match(without('available_from', '01/12/2026'), /start/);
    assert.match(without('location.country', ' '), /country/);
    assert.match(without('relocate', 'maybe'), /relocate/);
    assert.match(without('travel', ''), /travel/);
    assert.match(without('arrangement', 'office'), /work arrangement/);
    assert.match(without('work_authorization', ''), /right to work/);
    assert.match(without('languages', []), /language/);
    assert.match(without('languages', [{ language: 'French', level: 'fluent' }]), /level for French/);
    assert.match(cleanPractical('not json', role).error, /practical details/);
    assert.match(cleanPractical(null, role).error, /practical details/);
});

test('role requirements are cleaned, never rejected', () => {
    const l = cleanLogistics({ currency: 'usd', budget_min: 6000, budget_max: 4000, arrangement: 'office', start_date: 'soon',
        languages: [{ language: 'English', min_level: 'C1' }, { language: 'German', min_level: 'fluent' }] });
    assert.equal(l.currency, 'USD');
    assert.deepEqual([l.budget_min, l.budget_max], [4000, 6000]);        // swapped
    assert.equal(l.arrangement, '');
    assert.equal(l.start_date, '');
    assert.equal(l.budget_period, 'month');
    assert.equal(cleanLogistics({ languages: [{ language: 'German', min_level: 'fluent' }] }).languages.length, 0);
    assert.equal(hasLogistics(cleanLogistics({})), false);
    assert.equal(hasLogistics(l), true);
});

test('the public form never sees the budget', () => {
    const info = publicRoleInfo(role);
    assert.deepEqual(info, { id: 'R001', title: 'Data engineer', currency: 'EUR', work_country: 'France' });
});
