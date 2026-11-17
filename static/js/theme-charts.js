/* Recolor chart furniture, preserving dataset colors and the original theme options. */
(() => {
  'use strict';
  const originals = new WeakMap();
  Chart.register({
    id: 'bsnapCommandCenter',
    beforeUpdate(chart) {
      const active = document.documentElement.dataset.theme === 'command-center';
      if (!active && !originals.has(chart)) return;
      const options = chart.config.options;
      if (!originals.has(chart)) originals.set(chart, new Map());
      const saved = originals.get(chart);
      if (!active) {
        [...saved.values()].reverse().forEach(({ path, value, present }) => {
          const target = path.slice(0, -1).reduce((parent, key) => parent?.[key], options);
          if (!target) return;
          const key = path.at(-1);
          if (present) target[key] = value;
          else delete target[key];
        });
        originals.delete(chart);
        return;
      }
      const css = getComputedStyle(document.documentElement);
      // Chart.js rebuilds scale objects on update; restore by path rather than stale references.
      const set = (path, value) => {
        const target = path.slice(0, -1).reduce((parent, key) => parent[key], options);
        const key = path.at(-1);
        const id = JSON.stringify(path);
        if (!saved.has(id)) {
          saved.set(id, { path, value: target[key], present: Object.hasOwn(target, key) });
        }
        target[key] = value;
      };
      const child = path => {
        const value = path.reduce((parent, key) => parent?.[key], options);
        if (!value) set(path, {});
        return path;
      };
      const text = css.getPropertyValue('--ui-muted').trim();
      const border = css.getPropertyValue('--ui-border').trim();
      set(['color'], text);
      child(['plugins']);
      for (const key of ['legend', 'title', 'subtitle']) {
        if (options.plugins[key] === false) continue;
        const plugin = child(['plugins', key]);
        set([...(key === 'legend' ? child([...plugin, 'labels']) : plugin), 'color'], text);
      }
      if (options.plugins.tooltip !== false) {
        const tooltip = child(['plugins', 'tooltip']);
        set([...tooltip, 'backgroundColor'], css.getPropertyValue('--ui-surface').trim());
        set([...tooltip, 'titleColor'], css.getPropertyValue('--ui-text').trim());
        set([...tooltip, 'bodyColor'], text);
        set([...tooltip, 'borderColor'], border);
        set([...tooltip, 'borderWidth'], 1);
      }
      for (const key of Object.keys(options.scales || {})) {
        for (const part of ['ticks', 'title', 'grid', 'border']) {
          set([...child(['scales', key, part]), 'color'], ['grid', 'border'].includes(part) ? border : text);
        }
      }
    },
  });
  window.addEventListener('bsnap:themechange', () => {
    Object.values(Chart.instances).forEach(chart => chart.update('none'));
  });
})();
