'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {script, dates} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const all = [];
class Element {
  constructor() {
    this.dataset = {}; this.children = []; this.value = ''; this.className = '';
    this.classList = {add() {}, remove() {}, toggle() {}};
    this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
    all.push(this);
  }
  set textContent(value) { this.markup = escape(value); this.text = String(value ?? ''); }
  get textContent() { return this.text || ''; }
  set innerHTML(value) { this.markup = value; this.children = []; }
  get innerHTML() { return this.markup || ''; }
  get outerHTML() {
    const attrs = Object.entries(this.dataset).map(([key,value]) =>
      `data-${key.replace(/[A-Z]/g, letter => '-'+letter.toLowerCase())}="${escape(value)}"`).join(' ');
    return `<span ${attrs}>${this.innerHTML}</span>`;
  }
  appendChild(child) { this.children.push(child); }
  addEventListener() {}
}
const ids = new Map();
const events = new Map();
const document = {
  documentElement: {lang: 'en'},
  createElement() { return new Element(); },
  getElementById(id) { if (!ids.has(id)) ids.set(id, new Element()); return ids.get(id); },
  querySelectorAll() { return all.filter(element => element.dataset.localizedDate); },
  addEventListener() {},
};
const context = vm.createContext({document, Intl, Date, URL, URLSearchParams, console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  window: {addEventListener(event, fn) { events.set(event, fn); }},
});
vm.runInContext(dates, context);
context.BSnapDates = context.window.BSnapDates;
vm.runInContext(script, context);
const hostile = '<img src=x onerror="globalThis.pwned=true">';
context.renderLogEntries([{level: hostile, logger: hostile, pid: hostile, message: hostile,
  timestamp: '2026-10-07T16:30:00Z'}]);
const row = ids.get('logViewer').children[0].children[0].innerHTML;
assert(!row.includes('<img'), 'All log metadata must be escaped');
assert(row.includes('&lt;img'), 'Hostile values remain visible as text');
assert(row.includes('data-date-timezone="Asia/Makassar"'));

const iso = '2026-10-07T16:30:00Z';
const zones = [['Asia/Jakarta', '07', '23:30:00'],
  ['Asia/Makassar', '08', '00:30:00'], ['Asia/Jayapura', '08', '01:30:00']];
for (const lang of ['en', 'id', 'en']) {
  document.documentElement.lang = lang;
  for (const [timeZone, day, clock] of zones) {
    const markup = context.BSnapDates.html(iso, 'datetime', {timeZone});
    assert(markup.includes(day));
    assert(markup.includes(lang === 'id' ? clock.replaceAll(':', '.') : clock), markup);
    assert(markup.includes(lang === 'id' ? 'Okt' : 'Oct'), markup);
    const span = all.at(-1);
    assert.equal(span.dataset.dateTimezone, timeZone);
    const original = span.textContent;
    events.get('bsnap:languagechange')();
    assert.equal(span.textContent, original, 'Language refresh must retain the configured clock');
    const dateOnly = context.BSnapDates.html('2026-10-07', 'date', {timeZone});
    assert(dateOnly.includes('07'), 'Date-only values must not move to a different day');
  }
  assert(context.BSnapDates.format('07/10/2026 - 23:30:00 WIB').includes('23'));
}

(async () => {
  let headers;
  context.fetch = async (_url, options) => {
    headers = options.headers;
    return {ok: false, status: 403, json: async () => ({detail: hostile})};
  };
  await context.loadLogFiles('main');
  assert.equal(headers.Accept, 'application/json');
  assert(!ids.get('logFilesList').innerHTML.includes('<img'));
  assert(ids.get('logFilesList').innerHTML.includes('&lt;img'));
  context.fetch = async () => { throw new Error(hostile); };
  await context.loadLogFiles('main');
  assert(!ids.get('logFilesList').innerHTML.includes('<img'));
})().catch(error => { console.error(error); process.exitCode = 1; });
