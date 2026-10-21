/* Run directly with Node; no application server or external network is used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = name => fs.readFileSync(path.join(__dirname, '../../static/js', name), 'utf8');
const flush = () => new Promise(setImmediate);

function fixture() {
  const requests = [], states = [], received = [], timers = new Map();
  let sequence = 0;
  const window = {};
  const context = vm.createContext({
    window, AbortController, TypeError, SyntaxError,
    setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(Object.assign(new Error(), { name: 'AbortError' })));
      requests.push({ url, options, resolve, reject });
    })
  });
  vm.runInContext(script('map-data-loader.js'), context);
  const loader = window.BSnapMapData.create({ onData: data => received.push(data), onState: state => states.push(state) });
  const respond = (index, status = 200, body = [{ id: 'one' }], headers = {}) => requests[index].resolve({
    ok: status >= 200 && status < 300, status,
    headers: { get: name => headers[name] || null }, json: async () => body
  });
  const tick = () => {
    const [id, timer] = [...timers.entries()].sort((a, b) => a[1].delay - b[1].delay)[0];
    timers.delete(id); timer.callback(); return timer.delay;
  };
  return { loader, requests, states, received, timers, respond, tick };
}

async function dataTests() {
  {
    const f = fixture();
    const first = f.loader.refresh();
    f.loader.refresh();
    assert.equal(f.requests.length, 1, 'refresh is single-flight');
    f.respond(0, 503); await first;
    assert.equal(f.states.at(-1), 'retrying');
    assert.ok(f.tick() < 1500, 'recovery does not wait a full polling minute');
    f.respond(1); await flush();
    assert.equal(f.received.length, 1);
    assert.equal(f.states.at(-1), 'ready');
    assert.equal(f.tick(), 60000, 'polling scheduled from completion');
    f.requests[2].reject(new TypeError('network')); await flush();
    assert.equal(f.received.length, 1, 'failed refresh preserves existing marker data');
    f.loader.stop();
    assert.equal(f.timers.size, 0);
  }
  for (const status of [401, 403, 404]) {
    const f = fixture();
    const request = f.loader.refresh(); f.respond(0, status); await request;
    assert.equal(f.states.at(-1), 'error');
    assert.equal(f.timers.size, 0, 'permanent errors do not retry or poll');
    f.loader.retry(); assert.equal(f.requests.length, 2);
    f.respond(1); await flush();
    f.loader.stop();
  }
  {
    const f = fixture();
    f.loader.refresh();
    for (let i = 0; i < 4; i++) {
      f.respond(i, 502); await flush();
      const delay = f.tick();
      assert.ok(i < 3 ? delay < 6000 : delay === 60000, 'bounded retries then normal poll');
    }
    assert.equal(f.requests.length, 5);
    f.loader.stop(); await flush();
  }
  {
    const f = fixture(); f.loader.refresh();
    assert.equal(f.tick(), 30000, 'in-flight request has a timeout');
    await flush();
    assert.equal(f.requests[0].options.signal.aborted, true);
    assert.equal(f.states.at(-1), 'retrying');
    f.loader.stop();
  }
  {
    const f = fixture(); const pending = f.loader.refresh();
    f.respond(0, 429, null, { 'Retry-After': '10' }); await pending;
    assert.equal(f.tick(), 10000, 'rate-limit delay is honored');
    f.loader.stop(); await flush();
  }
  {
    const f = fixture(); const pending = f.loader.refresh();
    f.respond(0, 200, { detail: 'invalid' }); await pending;
    assert.equal(f.states.at(-1), 'error');
    assert.equal(f.received.length, 0);
    f.loader.stop();
  }
  {
    const f = fixture(); const pending = f.loader.refresh();
    f.loader.stop(); await pending;
    assert.equal(f.requests[0].options.signal.aborted, true);
    assert.equal(f.timers.size, 0);
    assert.equal(f.received.length, 0);
  }
}

async function libraryTests() {
  function fixture() {
    const window = {}, scripts = [], timers = new Map();
    let id = 0;
    const context = vm.createContext({
      window,
      setTimeout: callback => { timers.set(++id, callback); return id; },
      clearTimeout: timer => timers.delete(timer),
      document: {
        createElement: () => ({ remove() {} }),
        head: { appendChild: element => scripts.push(element) }
      }
    });
    vm.runInContext(script('map-libraries.js'), context);
    return { window, scripts, timers };
  }
  {
    const f = fixture();
    const pending = f.window.BSnapMapLibraries.load();
    f.window.L = { map() {}, control: {} };
    f.scripts[0].onload(); await flush();
    assert.equal(f.scripts.length, 4);
    f.scripts.slice(1).forEach(element => element.onerror());
    await pending;
    assert.ok(f.scripts.every(element => element.src.startsWith('/static/vendor/')));
    assert.equal(f.timers.size, 0, 'optional plugin failures settle without blocking core');
  }
  {
    const f = fixture(); const pending = f.window.BSnapMapLibraries.load();
    f.scripts[0].onerror(); await assert.rejects(pending);
    assert.equal(f.scripts.length, 1, 'plugins require a working core');
    const retried = f.window.BSnapMapLibraries.load();
    f.window.L = { map() {}, control: { fullscreen() {} }, markerClusterGroup() {}, heatLayer() {} };
    f.scripts[1].onload(); await retried;
    assert.equal(f.timers.size, 0, 'core loading can be retried without a page reload');
  }
}
(async () => {
  await dataTests(); await libraryTests();
  console.log('Map loading: bounded retry, timeout, polling, marker preservation, auth errors, cancellation and plugin recovery passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
