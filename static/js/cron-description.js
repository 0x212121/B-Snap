(function () {
  const records = new WeakMap();
  const previews = new Map();
  let nextId = 0;
  const text = (en, id) => document.documentElement.lang === 'id' ? id : en;

  function describe(expression) {
    const locale = document.documentElement.lang === 'id' ? 'id' : 'en';
    let description = cronstrue.toString(expression, {
      locale, use24HourTimeFormat: true, logicalAndDayFields: true,
      throwExceptionOnParseError: true
    });
    const everyMinute = expression.match(/^\*\/(\d+) \* \* \* \*$/);
    if (locale === 'en' && everyMinute && Number(everyMinute[1]) > 1) {
      const number = Number(everyMinute[1]);
      const ending = number % 100 >= 11 && number % 100 <= 13 ? 'th'
        : ({1:'st', 2:'nd', 3:'rd'}[number % 10] || 'th');
      description = `At every ${number}${ending} minute`;
    }
    return description.endsWith('.') ? description : `${description}.`;
  }

  async function preview(expression) {
    const cached = previews.get(expression);
    if (cached && Date.now() - cached.time < 60000) return cached.promise;
    const promise = (async () => {
      const response = await fetch(`/admin/jobs/api/cron-preview?expression=${encodeURIComponent(expression)}`, {
        headers: {'Accept': 'application/json'}
      });
      if (response.status === 422) return {invalid: true};
      if (!response.ok) throw new Error('Cron preview request failed');
      return response.json();
    })();
    previews.set(expression, {promise, time: Date.now()});
    try { return await promise; }
    catch (error) { previews.delete(expression); throw error; }
  }

  async function render(source, record) {
    const revision = ++record.revision;
    const expression = (source.matches('input') ? source.value : source.dataset.cronExpression).trim();
    if (!expression) {
      record.output.textContent = source.dataset.cronEmpty === 'required'
        ? text('Enter a five-field cron expression.', 'Masukkan ekspresi cron dengan lima kolom.')
        : text('The default schedule or interval will be used.', 'Jadwal atau interval default akan digunakan.');
      return;
    }
    record.output.textContent = text('Reading schedule…', 'Membaca jadwal…');
    try {
      const result = await preview(expression);
      if (record.revision !== revision || !source.isConnected) return;
      if (result.invalid) {
        record.output.textContent = text('Enter a valid five-field cron expression.', 'Masukkan ekspresi cron yang valid dengan lima kolom.');
        return;
      }
      const timezone = ({'Asia/Jakarta':'WIB', 'Asia/Makassar':'WITA', 'Asia/Jayapura':'WIT'}[result.timezone] || result.timezone);
      record.output.textContent = `${describe(result.expression)} (${timezone})`;
    } catch (_) {
      if (record.revision === revision && source.isConnected) {
        record.output.textContent = text('Unable to describe this schedule.', 'Penjelasan jadwal tidak dapat ditampilkan.');
      }
    }
  }

  function bind(source, force) {
    let record = records.get(source);
    if (record && !force) return;
    if (!record) {
      let output = source;
      if (source.matches('input')) {
        output = document.createElement('p');
        output.id = `cron-description-${++nextId}`;
        output.className = 'no-translate mt-1 text-xs text-gray-500 dark:text-gray-400';
        output.setAttribute('role', 'status');
        source.insertAdjacentElement('afterend', output);
        source.setAttribute('aria-describedby', [source.getAttribute('aria-describedby'), output.id].filter(Boolean).join(' '));
      } else {
        output.classList.add('no-translate');
      }
      record = {output, revision: 0, timer: null};
      records.set(source, record);
      source.addEventListener('input', () => {
        clearTimeout(record.timer);
        ++record.revision;
        record.timer = setTimeout(() => render(source, record), 200);
      });
      source.addEventListener('change', () => {
        clearTimeout(record.timer);
        render(source, record);
      });
    }
    clearTimeout(record.timer);
    render(source, record);
  }

  function scan(root, force = false) {
    const selector = 'input[data-cron-input], [data-cron-expression]';
    if (root.nodeType === Node.ELEMENT_NODE && root.matches(selector)) bind(root, force);
    if (root.querySelectorAll) root.querySelectorAll(selector).forEach(source => bind(source, force));
  }

  window.BSnapCron = {refresh: (root = document) => scan(root, true), describe};
  window.addEventListener('bsnap:languagechange', () => scan(document, true));
  document.addEventListener('DOMContentLoaded', () => {
    scan(document);
    new MutationObserver(changes => {
      changes.forEach(change => change.addedNodes.forEach(node => scan(node)));
    }).observe(document.body, {subtree: true, childList: true});
    document.addEventListener('reset', event => setTimeout(() => scan(event.target, true), 0));
  });
})();
