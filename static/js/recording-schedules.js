/* Camera archive management. API values are inserted only as text or DOM attributes. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('recording-form');
  const field = name => form.elements.namedItem(name);
  const t = (message, values) => window.BSnapI18n?.t(message, values) ?? message;
  let data = { items: [], cameras: [], page: 1, pages: 1, total: 0, page_size: 10, timezone: 'UTC' };
  let editing = null;
  let listingController = null;
  let selectedFilter = new URLSearchParams(location.search).get('camera_id') || '';
  let previousFocus = null;
  let previousOverflow = '';

  function node(tag, text, classes = '') {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    element.className = classes;
    return element;
  }
  function errorMessage(body) {
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map(error => error.msg).join('; ');
    return t('Request failed');
  }
  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body));
    return body;
  }
  function cameraOptions(select, includeAll, current) {
    select.replaceChildren();
    if (includeAll) select.add(new Option(t('All cameras'), ''));
    else select.add(new Option(t('Choose camera'), ''));
    data.cameras.forEach(camera => { const option = new Option(camera.name, camera.id); option.className = 'no-translate'; select.add(option); });
    select.value = current;
  }
  async function load(page = data.page) {
    listingController?.abort();
    const controller = new AbortController();
    listingController = controller;
    $('recording-error').classList.add('hidden');
    try {
      const listing = await request(`/admin/recording-schedules/api?${new URLSearchParams({ page, camera_id: selectedFilter })}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      data = listing;
      cameraOptions($('recording-filter'), true, selectedFilter);
      render();
    } catch (error) {
      if (controller.signal.aborted) return;
      $('recording-error').textContent = error.message;
      $('recording-error').classList.remove('hidden');
    } finally { if (listingController === controller) listingController = null; }
  }
  function renderPlan(item, cell) {
    const mode = { clips: 'Periodic clips', window: 'Time window', continuous: 'Continuous (24 hours)' };
    cell.append(node('div', t(mode[item.mode]), 'font-medium'));
    if (item.mode === 'clips') {
      cell.append(node('div', item.cron ? `${t('Cron')}: ${item.cron}` : t('Every {minutes} minutes', { minutes: item.interval_minutes }), 'text-xs text-gray-500 dark:text-gray-400'));
      cell.append(node('div', t('Clip: {seconds} seconds', { seconds: item.clip_seconds }), 'text-xs'));
    } else {
      if (item.mode === 'window') {
        const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
        cell.append(node('div', item.weekdays.map(day => t(days[day])).join(', '), 'text-xs'));
        cell.append(node('div', `${item.start_time}–${item.end_time}`, 'no-translate text-xs'));
      }
      cell.append(node('div', t('Segment: {seconds} seconds', { seconds: item.segment_seconds }), 'text-xs'));
    }
  }
  function render() {
    $('recording-timezone').textContent = data.timezone;
    if (data.storage) {
      const low = data.storage.free_mb < data.storage.reserve_mb;
      $('recording-resource-state').textContent = low ? t('Recording paused: free disk reserve reached.') : t('Recording capacity: {cameras} cameras. Free disk: {free} MiB; reserve: {reserve} MiB.', { cameras: data.storage.max_cameras, free: data.storage.free_mb, reserve: data.storage.reserve_mb });
      $('recording-resource-state').classList.toggle('text-red-600', low);
    }
    $('recording-column-zone').textContent = `(${data.timezone})`;
    const rows = $('recording-rows');
    rows.replaceChildren();
    const statuses = { scheduled: 'Scheduled', disabled: 'Disabled', starting: 'Starting', recording: 'Recording', camera_busy: 'Camera busy', error: 'Recording failed', interrupted: 'Interrupted' };
    data.items.forEach(item => {
      const row = node('tr', undefined, 'hover:bg-gray-50 dark:hover:bg-gray-800');
      const cells = Array.from({ length: 5 }, () => node('td', undefined, 'px-4 py-4 align-top'));
      cells[0].append(node('div', item.name, 'no-translate font-medium'), node('div', item.camera_name, 'no-translate text-xs text-gray-500 dark:text-gray-400'));
      renderPlan(item, cells[1]);
      cells[2].append(node('span', t(statuses[item.status] || item.status), item.status === 'error' ? 'text-red-600 dark:text-red-400' : 'font-medium'));
      if (item.last_error) cells[2].append(node('p', t(item.last_error), 'mt-1 max-w-xs text-xs text-red-600 dark:text-red-400'));
      [item.last_recorded_at, item.next_run_at].forEach(value => cells[3].append(node('div', value ? BSnapDates.format(value, { timeZone: data.timezone }) : '—', 'no-translate text-xs whitespace-nowrap')));
      const actions = node('div', undefined, 'flex flex-wrap gap-2');
      const edit = node('button', t('Edit'), 'ui-button ui-button-secondary');
      edit.type = 'button'; edit.addEventListener('click', () => openEditor(item));
      const remove = node('button', t('Delete'), 'ui-button ui-button-secondary');
      remove.type = 'button'; remove.addEventListener('click', () => removeSchedule(item));
      const gallery = node('a', t('Videos'), 'ui-button ui-button-secondary');
      gallery.href = `/videos?camera=${encodeURIComponent(item.camera_name)}`;
      actions.append(edit, remove, gallery); cells[4].append(actions);
      row.append(...cells); rows.append(row);
    });
    if (!data.items.length) {
      const cell = node('td', t('No recording schedules yet.'), 'px-4 py-10 text-center text-gray-500 dark:text-gray-400');
      cell.colSpan = 5; const row = node('tr'); row.append(cell); rows.append(row);
    }
    renderPagination();
  }
  function renderPagination() {
    const root = $('recording-pagination'); root.replaceChildren();
    const start = data.total ? (data.page - 1) * data.page_size + 1 : 0;
    const end = Math.min(data.page * data.page_size, data.total);
    root.append(node('span', t('Showing {start}-{end} of {total} results', { start, end, total: data.total })));
    root.append(node('span', t('Page {page} of {pages}', { page: data.page, pages: data.pages })));
    if (data.pages <= 1) return;
    const controls = node('nav', undefined, 'flex flex-wrap gap-1'); controls.setAttribute('aria-label', t('Pagination'));
    function button(label, page, disabled = false) {
      const element = node('button', label, page === data.page && /^\d+$/.test(label) ? 'ui-button ui-button-primary' : 'ui-button ui-button-secondary');
      element.type = 'button'; element.disabled = disabled;
      if (page === data.page && /^\d+$/.test(label)) element.setAttribute('aria-current', 'page');
      element.addEventListener('click', () => load(page)); controls.append(element);
    }
    button(t('First'), 1, data.page === 1); button(t('Previous'), data.page - 1, data.page === 1);
    const pages = new Set([1, data.pages]);
    for (let page = Math.max(1, data.page - 2); page <= Math.min(data.pages, data.page + 2); page++) pages.add(page);
    let previous = 0;
    [...pages].sort((a, b) => a - b).forEach(page => { if (previous && page > previous + 1) controls.append(node('span', '…', 'px-2 py-2')); button(String(page), page); previous = page; });
    button(t('Next'), data.page + 1, data.page === data.pages); button(t('Last'), data.pages, data.page === data.pages);
    root.append(controls);
  }
  function updateMode() {
    document.querySelectorAll('[data-recording-mode]').forEach(section => {
      const active = section.dataset.recordingMode === field('mode').value;
      section.classList.toggle('hidden', !active);
      section.querySelectorAll('input, select').forEach(input => { input.disabled = !active; });
    });
    const clips = field('mode').value === 'clips';
    document.querySelector('[data-recording-segment]').classList.toggle('hidden', clips);
    field('segment_seconds').disabled = clips;
    const cron = field('timing').value === 'cron';
    $('recording-interval').classList.toggle('hidden', cron);
    $('recording-cron').classList.toggle('hidden', !cron);
    field('interval_minutes').disabled = !clips || cron;
    field('cron').disabled = !clips || !cron; field('cron').required = clips && cron;
  }
  function openEditor(item = null) {
    previousFocus = document.activeElement;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    editing = item?.id || null; form.reset();
    cameraOptions(field('camera_id'), false, item?.camera_id || selectedFilter);
    if (item) {
      ['name', 'mode', 'interval_minutes', 'cron', 'clip_seconds', 'segment_seconds', 'start_time', 'end_time'].forEach(key => { field(key).value = item[key]; });
      field('enabled').checked = item.enabled;
      form.querySelectorAll('[name="weekdays"]').forEach(input => { input.checked = item.weekdays.includes(Number(input.value)); });
      field('timing').value = item.cron ? 'cron' : 'interval';
    }
    $('recording-modal-title').textContent = t(item ? 'Edit schedule' : 'Add schedule');
    $('recording-form-error').classList.add('hidden'); updateMode();
    $('recording-modal').classList.remove('hidden'); field('name').focus();
  }
  function closeEditor() { $('recording-modal').classList.add('hidden'); document.body.style.overflow = previousOverflow; previousFocus?.focus(); }
  async function save(event) {
    event.preventDefault();
    const payload = { name: field('name').value, camera_id: field('camera_id').value, mode: field('mode').value, enabled: field('enabled').checked };
    if (payload.mode === 'clips') {
      payload.clip_seconds = Number(field('clip_seconds').value);
      if (field('timing').value === 'cron') payload.cron = field('cron').value;
      else payload.interval_minutes = Number(field('interval_minutes').value);
    } else {
      payload.segment_seconds = Number(field('segment_seconds').value);
      if (payload.mode === 'window') {
        payload.weekdays = [...form.querySelectorAll('[name="weekdays"]:checked')].map(input => Number(input.value));
        payload.start_time = field('start_time').value;
        payload.end_time = field('end_time').value;
      }
    }
    $('save-recording').disabled = true;
    try {
      await request(`/admin/recording-schedules/api${editing ? `/${encodeURIComponent(editing)}` : ''}`, { method: editing ? 'PUT' : 'POST', body: JSON.stringify(payload) });
      closeEditor(); Toast.success(t('Recording schedule saved')); await load();
    } catch (error) { $('recording-form-error').textContent = error.message; $('recording-form-error').classList.remove('hidden'); }
    finally { $('save-recording').disabled = false; }
  }
  async function removeSchedule(item) {
    const result = await themedSwal({ title: t('Delete recording schedule?'), text: t('Archived videos are kept. Active recording will stop.'), icon: 'warning', showCancelButton: true, confirmButtonText: t('Delete'), cancelButtonText: t('Cancel') });
    if (!result.isConfirmed) return;
    try { await request(`/admin/recording-schedules/api/${encodeURIComponent(item.id)}`, { method: 'DELETE' }); Toast.success(t('Recording schedule deleted')); await load(); }
    catch (error) { Toast.error(error.message); }
  }
  $('add-recording').addEventListener('click', () => openEditor());
  $('close-recording').addEventListener('click', closeEditor); $('cancel-recording').addEventListener('click', closeEditor);
  field('mode').addEventListener('change', updateMode); field('timing').addEventListener('change', updateMode);
  form.addEventListener('submit', save);
  $('refresh-recordings').addEventListener('click', () => load());
  $('recording-filter').addEventListener('change', event => { selectedFilter = event.target.value; load(1); });
  document.addEventListener('keydown', event => {
    if ($('recording-modal').classList.contains('hidden')) return;
    if (event.key === 'Escape') closeEditor();
    if (event.key === 'Tab') {
      const focusable = [...$('recording-modal').querySelectorAll('button, input, select')].filter(element => !element.disabled && element.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  window.addEventListener('bsnap:languagechange', () => { render(); cameraOptions($('recording-filter'), true, selectedFilter); });
  setInterval(() => { if (!document.hidden && $('recording-modal').classList.contains('hidden')) load(); }, 15000);
  load(1);
})();
