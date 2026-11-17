/* Presentation only: retain each page's existing filter and request handlers. */
(() => {
  window.BSnapDialogFocus = (event, dialog) => {
    if (event.key !== 'Tab') return;
    const controls = Array.from(dialog.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), video[controls], [tabindex]:not([tabindex="-1"])'))
      .filter(element => element.getClientRects().length);
    if (!controls.length) { event.preventDefault(); return; }
    const first = controls[0], last = controls[controls.length - 1];
    if (!dialog.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
      event.preventDefault(); (event.shiftKey ? last : first).focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  };
  function initializeFilters() {
    document.querySelectorAll('[data-more-filters]').forEach((details) => {
      const counter = details.querySelector('[data-filter-count]');
      const filterBar = details.closest('.ui-filter-bar');
      if (!counter || !filterBar) return;
      const controls = [...details.querySelectorAll('input, select')];
      const update = () => {
        const active = controls.filter((control) => {
          if (control.type === 'checkbox' || control.type === 'radio') return control.checked;
          return control.value !== (control.dataset.filterDefault || '');
        }).length;
        counter.hidden = active === 0;
        counter.textContent = String(active);
        counter.setAttribute('aria-label', document.documentElement.lang === 'id'
          ? `${active} filter aktif` : `${active} active filters`);
        return active;
      };
      if (update() > 0) details.open = true;
      filterBar.addEventListener('input', update);
      filterBar.addEventListener('change', update);
      filterBar.addEventListener('click', () => queueMicrotask(update));
      window.addEventListener('bsnap:languagechange', update);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeFilters, { once: true });
  } else {
    initializeFilters();
  }
})();
