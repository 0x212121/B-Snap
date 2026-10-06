/* Presentation only: retain each page's existing filter and request handlers. */
(() => {
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
