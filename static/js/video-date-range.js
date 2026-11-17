(() => {
  'use strict';
  let picker;
  let fromId = 'videoDateFrom', toId = 'videoDateTo';
  const get = id => document.getElementById(id);
  const t = text => window.BSnapI18n?.t(text) || text;
  const displayDate = value => BSnapDates.format(value, { hour: undefined, minute: undefined, second: undefined });
  function sync() {
    const start = get(fromId).value;
    const end = get(toId).value;
    const formatRange = () => new Intl.DateTimeFormat(BSnapDates.locale(), {
      year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC',
    }).formatRange(new Date(`${start}T00:00:00Z`), new Date(`${end}T00:00:00Z`));
    get('videoDateRangeLabel').textContent = start && end && start <= end ? formatRange()
      : start && end ? `${displayDate(start)} – ${displayDate(end)}`
      : start ? `${t('From date')}: ${displayDate(start)}`
      : end ? `${t('To date')}: ${displayDate(end)}` : t('Select date range');
    if (picker) picker.setDate([start, end].filter(Boolean), false, 'Y-m-d');
  }
  function apply(start, end) {
    get(fromId).value = start;
    get(toId).value = end;
    sync();
    get(fromId).dispatchEvent(new Event('input', { bubbles: true }));
    get(toId).dispatchEvent(new Event('input', { bubbles: true }));
    get(fromId).dispatchEvent(new Event('change', { bubbles: true }));
    get('videoDateRange').open = false;
    get('videoDateRange').querySelector('summary').focus();
  }
  function today() {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: document.body.dataset.timezone || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  }
  function init() {
    const root = get('videoDateRange');
    fromId = root.dataset.fromId || fromId;
    toId = root.dataset.toId || toId;
    const calendar = document.createElement('input');
    calendar.type = 'text'; calendar.hidden = true;
    get('videoRangeCalendar').appendChild(calendar);
    picker = flatpickr(calendar, {
      inline: true, mode: 'range', dateFormat: 'Y-m-d', locale: document.documentElement.lang === 'id' ? 'id' : 'default',
      onChange(dates, _, instance) {
        if (dates.length === 2) apply(instance.formatDate(dates[0], 'Y-m-d'), instance.formatDate(dates[1], 'Y-m-d'));
      },
    });
    for (const id of [fromId, toId]) get(id).addEventListener('change', sync);
    root.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => {
      const end = today();
      const start = new Date(`${end}T00:00:00Z`);
      start.setUTCDate(start.getUTCDate() - Number(button.dataset.days) + 1);
      apply(start.toISOString().slice(0, 10), end);
    }));
    root.addEventListener('keydown', event => { if (event.key === 'Escape' && root.open) { root.open = false; root.querySelector('summary').focus(); } });
    document.addEventListener('click', event => { if (root.open && !root.contains(event.target)) root.open = false; });
    window.addEventListener('bsnap:languagechange', () => { picker.set('locale', document.documentElement.lang === 'id' ? 'id' : 'default'); sync(); });
    sync();
  }
  window.BSnapVideoDateRange = { init, sync };
})();
