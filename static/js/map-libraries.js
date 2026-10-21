/* Pinned same-origin libraries; optional plugins never prevent basic map markers. */
(function (global) {
  'use strict';
  function loadScript(src, ready, timeoutMs) {
    if (ready()) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      let settled = false;
      const finish = error => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        script.onload = script.onerror = null;
        if (error) { script.remove(); reject(error); } else resolve();
      };
      const timer = setTimeout(() => finish(new Error('Map library timeout')), timeoutMs);
      script.src = src;
      script.async = true;
      script.onload = () => finish(ready() ? null : new Error('Map library unavailable'));
      script.onerror = () => finish(new Error('Map library request failed'));
      document.head.appendChild(script);
    });
  }
  async function load() {
    await loadScript('/static/vendor/leaflet/leaflet.js', () => typeof global.L?.map === 'function', 15000);
    await Promise.allSettled([
      loadScript('/static/vendor/leaflet.markercluster/leaflet.markercluster.js', () => typeof global.L.markerClusterGroup === 'function', 3000),
      loadScript('/static/vendor/leaflet-fullscreen/Leaflet.fullscreen.min.js', () => typeof global.L.control.fullscreen === 'function', 3000),
      loadScript('/static/vendor/leaflet.heat/leaflet-heat.js', () => typeof global.L.heatLayer === 'function', 3000)
    ]);
  }
  global.BSnapMapLibraries = { load };
})(window);
