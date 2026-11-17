/* Shared theme state. Command Center uses dark utilities without changing their defaults. */
(() => {
  'use strict';
  const root = document.documentElement;
  const themes = ['light', 'dark', 'command-center'];
  const read = () => {
    try { return localStorage.getItem('theme'); } catch (_) { return null; }
  };
  function sync() {
    document.querySelectorAll('[data-theme-select]').forEach(select => {
      select.value = root.dataset.theme || (root.classList.contains('dark') ? 'dark' : 'light');
    });
    const favicon = document.querySelector('link[rel="icon"]');
    if (favicon && /^\/static\/icons\/favicon(?:-white)?\.ico$/.test(favicon.getAttribute('href'))) {
      favicon.href = root.classList.contains('dark')
        ? '/static/icons/favicon-white.ico' : '/static/icons/favicon.ico';
    }
  }
  function apply(theme, persist = false) {
    if (!themes.includes(theme)) return;
    root.dataset.theme = theme;
    root.classList.toggle('dark', theme !== 'light');
    if (persist) {
      try { localStorage.setItem('theme', theme); } catch (_) { /* Theme still works in this tab. */ }
    }
    sync();
    window.dispatchEvent(new CustomEvent('bsnap:themechange', { detail: { theme } }));
  }
  function init() {
    const saved = read();
    apply(themes.includes(saved) ? saved
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  }
  window.BSnapTheme = { init, set: theme => apply(theme, true) };
  // Standalone authentication pages keep their existing appearance unless this theme was chosen.
  if (read() === 'command-center') apply('command-center');
  document.addEventListener('DOMContentLoaded', sync);
  window.addEventListener('storage', event => {
    if (event.key === 'theme' || event.key === null) init();
  });
})();
