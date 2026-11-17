const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = name => fs.readFileSync(path.join(__dirname, '../../static/js', name), 'utf8');

function environment(saved, denied = false, systemDark = false) {
  const classes = new Set();
  const root = { dataset: {}, classList: {
    contains: name => classes.has(name),
    toggle(name, active) { active ? classes.add(name) : classes.delete(name); },
  } };
  const selects = [{}, {}];
  const listeners = {};
  const store = { theme: saved };
  const window = {
    matchMedia: () => ({ matches: systemDark }),
    addEventListener: (name, fn) => { listeners[name] = fn; },
    dispatchEvent: event => { listeners[event.type]?.(event); },
  };
  const context = vm.createContext({ window, document: {
    documentElement: root, querySelector: () => null, querySelectorAll: () => selects,
    addEventListener: (name, fn) => { listeners[name] = fn; },
  }, localStorage: {
    getItem: key => { if (denied) throw Error('denied'); return store[key]; },
    setItem: (key, value) => { if (denied) throw Error('denied'); store[key] = value; },
  }, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } } });
  vm.runInContext(source('theme.js'), context);
  return { context, root, selects, store, listeners, api: window.BSnapTheme };
}

test('saved themes and system defaults restore without overwriting the preference', () => {
  for (const theme of ['light', 'dark', 'command-center']) {
    const env = environment(theme, false, true);
    env.api.init();
    assert.equal(env.root.dataset.theme, theme);
    assert.equal(env.root.classList.contains('dark'), theme !== 'light');
    assert.equal(env.store.theme, theme);
  }
  for (const systemDark of [false, true]) {
    const env = environment('invalid', false, systemDark);
    env.api.init();
    assert.equal(env.root.dataset.theme, systemDark ? 'dark' : 'light');
    assert.equal(env.store.theme, 'invalid');
  }
});

test('desktop/mobile selectors, persistence, cross-tab changes and invalid input', () => {
  const env = environment('light');
  env.api.init();
  for (const theme of ['command-center', 'dark', 'light', 'command-center']) {
    env.api.set(theme);
    assert.equal(env.store.theme, theme);
    assert.ok(env.selects.every(select => select.value === theme));
  }
  env.api.set('untrusted');
  assert.equal(env.root.dataset.theme, 'command-center');
  env.store.theme = 'dark';
  env.listeners.storage({ key: 'theme' });
  assert.equal(env.root.dataset.theme, 'dark');
});

test('restricted storage still permits changing themes in the current tab', () => {
  const env = environment(null, true);
  env.api.init();
  env.api.set('command-center');
  assert.equal(env.root.dataset.theme, 'command-center');
});

test('chart options restore exactly and semantic dataset colors remain unchanged', () => {
  const env = environment('light');
  let plugin;
  const options = { scales: { y: { ticks: { color: '#123456' }, grid: { display: false } } },
    plugins: { legend: false, title: { color: 'red', text: 'Availability' } } };
  const before = JSON.stringify(options);
  const chart = { config: { options }, data: { datasets: [{ borderColor: 'green' }] } };
  env.context.Chart = { register: p => { plugin = p; }, instances: {} };
  env.context.getComputedStyle = () => ({ getPropertyValue: name => name === '--ui-muted' ? '#adbed0' : '#30445b' });
  vm.runInContext(source('theme-charts.js'), env.context);
  plugin.beforeUpdate(chart);
  assert.equal(JSON.stringify(options), before);
  for (let cycle = 0; cycle < 3; cycle++) {
    env.api.set('command-center');
    plugin.beforeUpdate(chart);
    plugin.beforeUpdate(chart);
    assert.equal(options.scales.y.ticks.color, '#adbed0');
    assert.equal(options.plugins.legend, false);
    assert.equal(chart.data.datasets[0].borderColor, 'green');
    // Chart.js replaces these objects while resolving defaults on each update.
    options.scales = structuredClone(options.scales);
    options.plugins = structuredClone(options.plugins);
    env.api.set('light');
    plugin.beforeUpdate(chart);
    assert.equal(JSON.stringify(options), before);
  }
});
