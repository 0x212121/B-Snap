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
      if (element.dataset.dateTimezone) options.timeZone = element.dataset.dateTimezone;
      const text = format(element.dataset.localizedDate, options);
      if (element.textContent !== text) element.textContent = text;
    }
  }
  function html(value, style = 'datetime', options = {}) {
    const span = document.createElement('span');
    span.dataset.localizedDate = value || '';
    span.dataset.dateStyle = style;
    if (options.timeZone) span.dataset.dateTimezone = options.timeZone;
    span.textContent = format(value, { ...options, ...(style === 'date'
      ? { hour: undefined, minute: undefined, second: undefined } : {}) });
    return span.outerHTML;
  }
  function parseLocal(value, { timeZone, disambiguation = 'earlier' } = {}) {
    // datetime-local is a wall clock, not the browser's local timezone.
    const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
    if (!match || !timeZone) return null;
    const [year, month, day, hour, minute, second] = match.slice(1, 7).map(part => Number(part || 0));
    const wall = new Date(0);
    wall.setUTCFullYear(year, month - 1, day);
    wall.setUTCHours(hour, minute, second, 0);
    if (wall.getUTCFullYear() !== year || wall.getUTCMonth() !== month - 1 ||
        wall.getUTCDate() !== day || wall.getUTCHours() !== hour ||
        wall.getUTCMinutes() !== minute || wall.getUTCSeconds() !== second) return null;
    try {
      const formatter = new Intl.DateTimeFormat('en-US-u-ca-iso8601-nu-latn', {
        timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
      });
      const clockAt = instant => {
        const parts = Object.fromEntries(formatter.formatToParts(instant)
          .filter(part => part.type !== 'literal').map(part => [part.type, Number(part.value)]));
        const clock = new Date(0);
        clock.setUTCFullYear(parts.year, parts.month - 1, parts.day);
        clock.setUTCHours(parts.hour, parts.minute, parts.second, 0);
        return clock.getTime();
      };
      const offsets = new Set([-36, 0, 36].map(hours => {
        const sample = wall.getTime() + hours * 3600000;
        return clockAt(sample) - sample;
      }));
      const candidates = [...offsets].map(offset => wall.getTime() - offset)
        .filter(instant => clockAt(instant) === wall.getTime()).sort((a, b) => a - b);
      // Gaps are invalid; an inclusive range can span both occurrences of a DST fold.
      if (!candidates.length) return null;
      const instant = disambiguation === 'later' ? candidates.at(-1) : candidates[0];
      return new Date(instant + Number((match[7] || '').padEnd(3, '0')));
    } catch (_) { return null; }
  }
  window.BSnapDates = { locale, format, html, update, parseLocal };
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
