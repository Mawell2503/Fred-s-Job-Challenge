// node --test tests/   (no dependencies)
import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanPractical, cleanLogistics, cleanCountries, hasLogistics, publicRoleInfo, upgradePractical } from '../practical.js';

const role = { id: 'R001', title: 'Data engineer', logistics: cleanLogistics({ currency: 'eur', work_country: 'France', budget_max: 5000 }) };
const good = {
    salary: { amount: '4500', period: 'month' },
    available_from: '2026-12-01',
    notice_weeks: '4',
    location: { country: 'Mauritius', city: 'Port Louis' },
    relocate: 'open', travel: 'occasional', arrangement: 'hybrid', work_countries: 'Mauritius, france, Mauritius',
    languages: [{ language: 'French', level: 'C1' }, { language: 'french', level: 'B1' }, { language: '', level: '' }],
};

test('a complete answer is accepted and normalised', () => {
    const { value, error } = cleanPractical(JSON.stringify(good), role);
    assert.equal(error, undefined);
    assert.deepEqual(value.salary, { amount: 4500, period: 'month', currency: 'EUR' });
    assert.equal(value.notice_weeks, 4);
    assert.deepEqual(value.languages, [{ language: 'French', level: 'C1' }]);   // duplicate + empty row dropped
    assert.deepEqual(value.work_countries, ['Mauritius', 'france']);              // duplicate dropped
    assert.equal('work_authorization' in value, false);
});

test('the applicant chooses the salary currency; without one it follows the role, else EUR', () => {
    const withCur = cur => cleanPractical({ ...good, salary: { ...good.salary, currency: cur } }, role).value.salary.currency;
    assert.equal(withCur('inr'), 'INR');
    assert.equal(withCur('rupees'), 'EUR');                                     // not a code: the role's currency
    assert.equal(cleanPractical(good, null).value.salary.currency, 'EUR');       // open application, nothing chosen
});

test('countries are split, trimmed and capped', () => {
    assert.deepEqual(cleanCountries(' Mauritius ;France\n,, '), ['Mauritius', 'France']);
    assert.equal(cleanCountries(Array.from({ length: 15 }, (_, i) => `C${i}`)).length, 10);
});

test('answers from before the pool get the countries they could work in', () => {
    const yes = { work_authorization: 'yes' }, no = { work_authorization: 'sponsorship' };
    assert.equal(upgradePractical(yes, role), true);
    assert.deepEqual(yes.work_countries, ['France']);
    upgradePractical(no, role);
    assert.deepEqual(no.work_countries, []);
    assert.equal(upgradePractical(yes, role), false);                           // only once
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
    assert.match(without('work_countries', ' , '), /countries you can work in/);
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

test('a start date must be a real calendar day', () => {
    const at = d => cleanPractical({ ...good, available_from: d }, role).error;
    assert.equal(at('2027-02-28'), undefined);
    assert.equal(at('2028-02-29'), undefined);                                   // leap year
    for (const bad of ['2026-02-31', '2027-02-29', '2026-04-31', '2026-13-01', '2026-00-10', '2026-1-5']) {
        assert.match(at(bad), /earliest date/, bad);
    }
    assert.equal(cleanLogistics({ start_date: '2026-02-31' }).start_date, '');   // the role's date too
});

test('an incomplete language row on the role drops only that row', () => {
    const l = cleanLogistics({ languages: [{ language: 'French', min_level: 'B2' }, { language: 'English', min_level: '' },
        { language: '', min_level: 'C1' }, { language: 'Spanish', min_level: 'Z9' }, { language: 'German', min_level: 'A2' }] });
    assert.deepEqual(l.languages, [{ language: 'French', min_level: 'B2' }, { language: 'German', min_level: 'A2' }]);
    // the applicant, though, must fix an incomplete row
    assert.match(cleanPractical({ ...good, languages: [{ language: 'English', level: '' }] }, role).error, /level for English/);
});
