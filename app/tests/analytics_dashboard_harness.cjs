const assert = require('node:assert/strict');
const vm = require('node:vm');
const { script, scenario } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));

class Element {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.disabled = true;
    this.classList = { add() {}, remove() {}, toggle() {} };
    this.style = {};
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  add(option) { this.children.push(option); if (option.selected) this.value = option.value; }
  getContext() { return this; }
}
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, new Element(id));
  return elements.get(id);
}
element('analyticsGroupFilter').value = '3';
element('cameraDropdown').value = 'Camera <one>';
element('periodDropdown').value = '14';
const listeners = new Map();
const requests = [];
const charts = new Set();
let ready;
const initialUrl = scenario === 'snapshots'
  ? 'http://test/analytics?tab=snapshots&days=14&group_id=3&camera=Camera'
  : 'http://test/analytics';
const window = {
  location: { href: initialUrl, search: new URL(initialUrl).search },
  history: { replaceState(_state, _title, url) {
    window.location.href = String(url);
    window.location.search = new URL(url).search;
  } },
  addEventListener(name, callback) {
    if (!listeners.has(name)) listeners.set(name, []);
    listeners.get(name).push(callback);
  },
  dispatchEvent(event) { (listeners.get(event.type) || []).forEach(callback => callback(event)); },
};
const context = vm.createContext({
  console: { log() {}, warn() {}, error() {} },
  window, URL, URLSearchParams, AbortController, Intl, Date,
  CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
  Option: class { constructor(text, value, _defaultSelected, selected) { Object.assign(this, { text, value, selected }); } },
  BSnapReportCharts: { localize() {} },
  BSnapDates: { format: value => value },
  Chart: class {
    constructor(target) { this.id = target.id; charts.add(this.id); }
    destroy() { charts.delete(this.id); }
  },
  document: {
    documentElement: { lang: 'en' },
    getElementById: element,
    createElement: () => new Element(),
    createTextNode: text => ({ textContent: text }),
    addEventListener(name, callback) { if (name === 'DOMContentLoaded') ready = callback; },
  },
  fetch(url, options) {
    assert.equal(options.headers.Accept, 'application/json');
    let resolve;
    const promise = new Promise(callback => { resolve = callback; });
    requests.push({ url, options, respond(data, ok = true) {
      resolve({ ok, status: ok ? 200 : 500, json: async () => data });
    } });
    return promise;
  },
});

const dashboard = count => ({
  summary: {
    totals: { api: count, command: 2, email: 3 },
    delta_pct: { api: 0, command: 0, email: 0 },
    activity_index: { value: 50, label: 'Stable', color: 'grey' },
  },
  daily: { api: [], command: [], email: [] }, top_commands: [], top_cameras: [],
});
const operations = {
  dates: [], availability: [], downtime_hours: [], coverage_hours: [],
  offline_incidents: [], top_downtime_cameras: [],
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const request = prefix => requests.find(item => item.url.startsWith(prefix));

async function main() {
  vm.runInContext(script, context);
  ready();
  if (scenario === 'snapshots') {
    assert.equal(requests.length, 4);
    assert(!request('/log/stats/'));
    const params = new URL(request('/stats/data').url, initialUrl).searchParams;
    assert.equal(params.get('camera'), 'Camera <one>');
    assert.equal(params.get('days'), '14');
    assert.equal(params.get('group_id'), '3');
    request('/stats/snapshot-details').respond({
      camera_names: ['Camera <one>', '<img src=x onerror=alert(1)>'],
      timing_logs: [{ started_at: '02/10/2026 - 00:30:00 WITA', ended_at: '02/10/2026 - 00:31:01 WITA', duration: '00:01:01', status: 'success' }],
    });
    request('/stats/data').respond({ chart_labels: ['2026-10-01'], chart_data: [5] });
    request('/stats/no-data-cameras').respond({ cameras: [] });
    request('/storage/status').respond({ alert_level: 'unknown', usage_percent: 0 });
    await flush();
    assert.equal(element('cameraDropdown').value, 'Camera <one>');
    assert(element('cameraDropdown').children.some(option => option.text === '<img src=x onerror=alert(1)>'));
    assert.equal(element('snapshotTimingBody').children.length, 1);
    window.setAnalyticsTab('snapshots');
    await flush();
    assert.equal(requests.length, 4, 'Already loaded snapshot data should not be requested twice');
    window.setAnalyticsTab('overview');
    assert.equal(requests.length, 6);
    return;
  }
  assert.equal(requests.length, 2, 'Overview must not request snapshots or storage');
  const activity = request('/log/stats/dashboard');
  const ops = request('/stats/operations');
  if (scenario === 'stale') {
    element('endDate').value = '2026-10-20';
    element('startDate').value = '2026-10-01';
    element('applyFilter').listeners.click();
    assert(activity.options.signal.aborted);
    assert(ops.options.signal.aborted);
    requests[2].respond(dashboard(42));
    requests[3].respond(operations);
    await flush();
    activity.respond(dashboard(999));
    ops.respond(operations);
    await flush();
    assert.equal(element('kpiApi').textContent, '42');
    assert.equal(element('downloadReport').disabled, false);
    return;
  }
  if (scenario === 'operations_first' || scenario === 'activity_failure') {
    ops.respond(operations);
    await flush();
    assert(charts.has('chartCameraAvailability'));
    assert(!charts.has('chartApiCommand'));
    assert.equal(element('downloadReport').disabled, true);
    activity.respond(scenario === 'activity_failure' ? { detail: 'Temporary failure' } : dashboard(42),
      scenario !== 'activity_failure');
  } else {
    activity.respond(dashboard(42));
    await flush();
    assert.equal(element('kpiApi').textContent, '42');
    assert(!charts.has('chartCameraAvailability'));
    assert.equal(element('downloadReport').disabled, true);
    ops.respond(scenario === 'partial_failure' ? { detail: 'Temporary failure' } : operations,
      scenario !== 'partial_failure');
  }
  await flush();
  if (scenario === 'activity_failure') {
    assert(charts.has('chartCameraAvailability'));
    assert.equal(element('downloadReport').disabled, true);
    assert(element('overviewStatus').textContent.includes('Unable to refresh system activity.'));
    return;
  }
  assert.equal(element('kpiApi').textContent, '42');
  if (scenario === 'partial_failure') {
    assert.equal(element('downloadReport').disabled, true);
    assert(element('overviewStatus').textContent.includes('Unable to refresh camera operations.'));
  } else {
    assert.equal(element('downloadReport').disabled, false);
    assert.equal(element('overviewStatus').textContent, '');
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
