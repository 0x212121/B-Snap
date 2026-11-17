const assert = require('node:assert/strict');
const vm = require('node:vm');
const { script } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    innerHTML: '', textContent: '',
    classList: { add() {}, remove() {}, toggle() {} },
    setAttribute() {},
  });
  return elements.get(id);
}
const context = vm.createContext({
  document: { addEventListener() {}, getElementById: element },
  window: {}, console, encodeURIComponent, decodeURIComponent,
  BSnapSizes: { format: () => '10 B' },
  BSnapDates: { html: value => `<span data-localized-date="${value}">${value}</span>` },
  Swal: { isVisible: () => false },
});
vm.runInContext(script, context);
const malicious = `x'\"><img src=x onerror=alert(1)>`;
const item = {
  id: malicious, file_path: `camera/${malicious}.jpg`, camera: malicious,
  deleted_at: '03/11/2026 - 23:30:00 WITA', deleted_at_iso: '2026-11-03T15:30:00Z',
  file_size: 10, resolution: malicious, file_hash: malicious,
  retention_hold: true, retention_hold_reason: malicious, duration: malicious,
};
for (const [render, target] of [['renderSnapshots', 'snapshots-body'], ['renderVideos', 'videos-body']]) {
  context[render]([item], 1, 1);
  const html = element(target).innerHTML;
  assert.ok(!html.includes('<img src=x onerror=alert(1)>'), html);
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes(`data-id="${encodeURIComponent(item.id)}"`));
  assert.ok(html.includes('data-sort-value="2026-11-03T15:30:00Z"'));
  assert.ok(html.includes('data-localized-date="03/11/2026 - 23:30:00 WITA"'));
  assert.match(html, /disabled[^>]*aria-label="Purge Permanently"/);
}
