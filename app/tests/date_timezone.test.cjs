const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const window = { addEventListener() {} };
const document = { documentElement: {lang: 'en'}, addEventListener() {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/date-format.js'), 'utf8'), {window, document, Intl, Date, MutationObserver: class {}});
const dates = window.BSnapDates;

for (const zone of ['UTC', 'America/Los_Angeles', 'Asia/Jakarta']) {
    test(`configured local bounds are UTC instants independent of process timezone ${zone}`, () => {
        process.env.TZ = zone;
        for (const [timeZone, expected] of [
            ['Asia/Jakarta', '2026-11-02T17:30:00.000Z'],
            ['Asia/Makassar', '2026-11-02T16:30:00.000Z'],
            ['Asia/Jayapura', '2026-11-02T15:30:00.000Z'],
            ['Asia/Kathmandu', '2026-11-02T18:45:00.000Z'],
        ]) {
            assert.equal(dates.parseLocal('2026-11-03T00:30', {timeZone}).toISOString(), expected);
        }
    });
}
test('DST folds cover both occurrences and gaps reject nonexistent clocks', () => {
    const timeZone = 'America/New_York';
    assert.equal(dates.parseLocal('2026-11-01T01:30', {timeZone}).toISOString(), '2026-11-01T05:30:00.000Z');
    assert.equal(dates.parseLocal('2026-11-01T01:30', {timeZone, disambiguation: 'later'}).toISOString(), '2026-11-01T06:30:00.000Z');
    assert.equal(dates.parseLocal('2026-03-08T02:30', {timeZone}), null);
});
test('invalid input and zones reject rather than using browser timezone', () => {
    for (const value of ['', '2026-02-30T12:00', '2026-11-03T24:30', '2026-11-03', 'bad']) {
        assert.equal(dates.parseLocal(value, {timeZone: 'Asia/Makassar'}), null);
    }
    assert.equal(dates.parseLocal('2026-11-03T00:30'), null);
    assert.equal(dates.parseLocal('2026-11-03T00:30', {timeZone: 'bad'}), null);
    assert.equal(dates.parseLocal('2026-11-03T00:30:01.25', {timeZone: 'UTC'}).toISOString(), '2026-11-03T00:30:01.250Z');
});
test('language changes format without changing instants, date-only or backend-local clocks', () => {
    for (const lang of ['en', 'id', 'en']) {
        document.documentElement.lang = lang;
        const actual = dates.format('2026-11-02T16:30:00Z', {timeZone: 'Asia/Makassar'});
        const expected = new Intl.DateTimeFormat(lang === 'id' ? 'id-ID' : 'en-US', {timeZone: 'Asia/Makassar', year:'numeric', month:'short', day:'2-digit', hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date('2026-11-02T16:30:00Z'));
        assert.equal(actual, expected);
        assert.ok(dates.format('03/11/2026 - 00:30:00 WITA').endsWith(' WITA'));
        assert.ok(dates.format('2026-11-03', {hour:undefined,minute:undefined,second:undefined}).includes('03'));
    }
});
