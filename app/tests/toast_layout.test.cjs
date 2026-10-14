const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function setup() {
    const elements = [];
    const timers = new Map();
    const frames = [];
    const listeners = {};
    let timerId = 0;
    let resize;
    const context = {
        document: {
            readyState: 'loading',
            addEventListener() {},
            body: { appendChild(element) { elements.push(element); } },
            createElement() {
                return {
                    style: {}, attributes: {}, offsetHeight: 80,
                    setAttribute(name, value) { this.attributes[name] = value; },
                    getAttribute(name) { return this.attributes[name]; },
                    querySelector() { return null; },
                    remove() { elements.splice(elements.indexOf(this), 1); }
                };
            },
            querySelectorAll(selector) {
                const position = selector.match(/data-position="([^"]+)"/)[1];
                return elements.filter(element => element.attributes['data-position'] === position);
            }
        },
        window: { addEventListener(name, callback) { listeners[name] = callback; } },
        ResizeObserver: class {
            constructor(callback) { resize = callback; }
            observe() {}
            unobserve() {}
        },
        requestAnimationFrame(callback) { frames.push(callback); },
        setTimeout(callback, delay) {
            timers.set(++timerId, { callback, delay });
            return timerId;
        },
        clearTimeout(id) { timers.delete(id); }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/toast.js'), 'utf8'), context);
    return {
        toast: context.window.Toast, elements, listeners,
        resize() { resize(); },
        animate() { frames.splice(0).forEach(callback => callback()); },
        finishDismissals() {
            for (const [id, timer] of [...timers]) {
                if (timer.delay === 300) {
                    timers.delete(id);
                    timer.callback();
                }
            }
        }
    };
}

for (const position of ['top-right', 'top-left', 'top-center', 'bottom-right', 'bottom-left', 'bottom-center']) {
    test(`variable heights and dismissal preserve spacing at ${position}`, () => {
        const state = setup();
        const ids = [1, 2, 3].map(() => state.toast.info('Message', { position, persistent: true }));
        state.elements[0].offsetHeight = 150;
        state.elements[1].offsetHeight = 210;
        state.resize();
        const edge = position.startsWith('bottom') ? 'bottom' : 'top';
        assert.deepEqual(state.elements.map(element => element.style[edge]), ['20px', '186px', '412px']);
        state.animate();
        if (position.endsWith('center')) {
            assert.equal(state.elements[0].style.transform, 'translateX(-50%)');
        }
        assert.ok(state.elements.every(element => !element.style.transition.includes('all')));
        state.toast.dismiss(ids[1]);
        // Reserve the departing toast's space until its animation finishes.
        assert.equal(state.elements[2].style[edge], '412px');
        state.finishDismissals();
        assert.deepEqual(state.elements.map(element => element.style[edge]), ['20px', '186px']);
        state.elements[0].offsetHeight = 250;
        state.listeners.resize();
        assert.equal(state.elements[1].style[edge], '286px');
        state.elements[0].offsetHeight = 300;
        state.listeners['bsnap:languagechange']();
        assert.equal(state.elements[1].style[edge], '336px');
    });
}

test('rapid notifications evict successive oldest toasts and dismissAll stays safe', () => {
    const state = setup();
    for (let index = 0; index < 12; index++) state.toast.info('Burst', { persistent: true });
    state.animate();
    state.finishDismissals();
    assert.equal(state.elements.length, 5);
    assert.deepEqual(state.elements.map(element => element.id), ['toast-8', 'toast-9', 'toast-10', 'toast-11', 'toast-12']);
    assert.ok(state.elements.every(element => element.style.opacity === '1'));
    state.toast.dismissAll();
    state.toast.dismissAll();
    state.finishDismissals();
    assert.equal(state.elements.length, 0);
});

test('unsupported positions use the default stack', () => {
    const state = setup();
    state.toast.info('Message', { position: 'unknown', persistent: true });
    assert.equal(state.elements[0].attributes['data-position'], 'top-right');
});
