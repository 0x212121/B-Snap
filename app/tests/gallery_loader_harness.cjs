/* Isolated DOM/request harness: node app/tests/gallery_loader_harness.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() {
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attributes = {};
    this.classes = new Set();
    this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name) };
    this.bounds = { top: 5000, bottom: 5100 };
  }
  set innerHTML(value) {
    this.children = [...value.matchAll(/data-id="([^"]+)"/g)].map(match => {
      const item = new Element();
      item.dataset.id = match[1];
      return item;
    });
  }
  querySelectorAll(selector) { return selector === 'img' ? [] : this.children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  appendChild(child) { this.children.push(...(child.fragment ? child.children : [child])); }
  after(child) { this.retry = child; }
  getBoundingClientRect() { return this.bounds; }
}

function fixture(pagination = 'page') {
  const grid = new Element(), sentinel = new Element();
  const requests = [], frames = [], timers = new Map(), errors = [];
  let timerId = 0, observe;
  const state = { currentPage: 1, offset: 2, hasMore: true, isLoading: false, lastRequestId: 0 };
  const window = {
    innerHeight: 800, location: { href: 'http://test/videos' },
    requestAnimationFrame: callback => frames.push(callback)
  };
  const context = vm.createContext({
    window, URL, URLSearchParams, AbortController, Event,
    document: {
      createElement: () => new Element(),
      createDocumentFragment: () => Object.assign(new Element(), { fragment: true })
    },
    IntersectionObserver: class {
      constructor(callback) { observe = callback; }
      observe() {}
    },
    Toast: { error: message => errors.push(message) },
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }))
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/js/gallery-loader.js'), 'utf8'), context);
  const loader = window.BSnapGallery.create({
    state, grid, sentinel, selector: '[data-card]', pageSize: 2,
    pagination, url: '/gallery', params: () => new URLSearchParams(),
    errorMessage: 'Failed', placeholder: '/placeholder.svg'
  });
  const respond = (index, ids, ok = true) => requests[index].resolve({
    ok, status: ok ? 200 : 503,
    json: async () => ({ html: ids.map(id => `<div data-id="${id}"></div>`).join('') })
  });
  return { loader, state, grid, sentinel, requests, frames, timers, errors, respond, observe: () => observe() };
}

async function run() {
  for (const mode of ['page', 'offset']) {
    const f = fixture(mode);
    const first = f.loader.loadMore();
    f.respond(0, [], false);
    await first;
    assert.equal(f.state.currentPage, 1);
    assert.equal(f.state.offset, 2);
    f.observe();
    assert.equal(f.requests.length, 1, 'failed request pauses automatic retries');
    f.sentinel.retry.listeners.click();
    assert.equal(f.requests[0].url, f.requests[1].url, 'retry uses the same cursor');
    assert.equal(f.requests[1].options.headers.Accept, 'application/json');
    f.respond(1, ['3', '4']);
    await new Promise(setImmediate);
    assert.equal(f.state.currentPage, 2);
    assert.equal(f.state.offset, 4);
    assert.equal(f.sentinel.retry.hidden, true);
    assert.equal(f.timers.size, 0, 'request timeout cleaned up');
    f.sentinel.bounds = { top: 700, bottom: 800 };
    f.frames.shift()();
    assert.equal(f.requests.length, 3, 'completion rechecks a still-visible sentinel');
    f.observe();
    assert.equal(f.requests.length, 3, 'only one active append request');
    f.respond(2, ['4']);
    await new Promise(setImmediate);
    assert.equal(f.grid.children.length, 2, 'overlapping IDs do not duplicate cards');
    assert.equal(f.state.hasMore, false);
    assert.equal(f.sentinel.classes.has('hidden'), true);
  }
  {
    const f = fixture();
    const old = f.loader.loadMore();
    const fresh = f.loader.reset();
    assert.equal(f.requests[0].options.signal.aborted, true);
    f.respond(1, ['new']);
    await fresh;
    f.respond(0, ['stale']);
    await old;
    assert.equal(f.grid.children[0].dataset.id, 'new', 'stale response cannot overwrite filter results');
    assert.equal(f.state.offset, 1);
    assert.equal(f.state.currentPage, 1);
  }
  {
    const f = fixture();
    const pending = f.loader.loadMore();
    f.requests[0].resolve({ ok: true, json: async () => ({ detail: 'invalid' }) });
    await pending;
    assert.equal(f.state.currentPage, 1, 'malformed response cannot advance cursor');
    assert.equal(f.errors.length, 1);
  }
  {
    const f = fixture();
    const pending = f.loader.loadMore();
    [...f.timers.values()][0].callback();
    f.requests[0].reject(Object.assign(new Error('timeout'), { name: 'AbortError' }));
    await pending;
    assert.equal(f.state.isLoading, false);
    assert.equal(f.sentinel.retry.hidden, false, 'timeout remains retryable');
  }
  {
    const f = fixture();
    const img = { tagName: 'IMG', dataset: {}, src: 'http://test/secure/1?thumb=true', isConnected: true };
    for (let index = 0; index < 2; index++) {
      f.grid.listeners.error({ target: img });
      const [id, timer] = [...f.timers.entries()][0];
      f.timers.delete(id);
      timer.callback();
      assert.match(img.src, /thumb=true/);
    }
    f.grid.listeners.error({ target: img });
    assert.equal(img.src, '/placeholder.svg');
    f.grid.listeners.error({ target: img });
    assert.equal(f.timers.size, 0, 'fallback never enters an error loop');
  }
  console.log('Gallery loader: pagination, retry, sentinel, stale responses, deduplication, timeout and image recovery passed.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
