/* Date display follows the application's selected language. */
(() => {
  'use strict';
  const locale = () => document.documentElement.lang === 'id' ? 'id-ID' : 'en-US';
  function format(value, options = {}) {
    if (!value) return '-';
    const original = String(value);
    // Backend timestamps are already in the configured timezone. Preserve that clock and label.
    const local = original.match(/^(\d{2})\/(\d{2})\/(\d{4}) - (\d{2}):(\d{2}):(\d{2}) (.+)$/);
    const date = local
      ? new Date(Date.UTC(+local[3], +local[2] - 1, +local[1], +local[4], +local[5], +local[6]))
      : new Date(value);
    if (Number.isNaN(date.getTime())) return original;
    const settings = {
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      ...options,
    };
    if (local || /^\d{4}-\d{2}-\d{2}$/.test(original)) settings.timeZone = 'UTC';
    const formatted = new Intl.DateTimeFormat(locale(), settings).format(date);
    return local ? `${formatted} ${local[7]}` : formatted;
  }
  function update(root = document) {
    const elements = [];
    if (root.matches?.('[data-localized-date]')) elements.push(root);
    elements.push(...(root.querySelectorAll?.('[data-localized-date]') || []));
    for (const element of elements) {
      const options = element.dataset.dateStyle === 'date'
        ? { hour: undefined, minute: undefined, second: undefined } : {};
      const text = format(element.dataset.localizedDate, options);
      if (element.textContent !== text) element.textContent = text;
    }
  }
  function html(value, style = 'datetime') {
    const span = document.createElement('span');
    span.dataset.localizedDate = value || '';
    span.dataset.dateStyle = style;
    span.textContent = format(value, style === 'date'
      ? { hour: undefined, minute: undefined, second: undefined } : {});
    return span.outerHTML;
  }
  window.BSnapDates = { locale, format, html, update };
  window.addEventListener('bsnap:languagechange', () => update());
  document.addEventListener('DOMContentLoaded', () => {
    update();
    new MutationObserver(records => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node.nodeType === 1) update(node);
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  });
})();
