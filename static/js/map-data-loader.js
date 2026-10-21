/* Single-flight polling with bounded recovery for read-only camera locations. */
(function (global) {
  'use strict';
  function create({ onData, onState, timeoutMs = 30000, refreshMs = 60000 }) {
    let timer = null;
    let active = null;
    let stopped = false;
    let retries = 0;
    const delays = [1000, 2000, 4000];
    function schedule(delay) {
      clearTimeout(timer);
      timer = setTimeout(refresh, delay);
    }
    async function refresh() {
      if (stopped || active) return;
      clearTimeout(timer);
      const controller = new AbortController();
      active = controller;
      let timedOut = false;
      const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
      onState('loading');
      try {
        const response = await fetch('/camera-locations', {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
          signal: controller.signal
        });
        if (!response.ok) {
          const error = new Error(`Camera locations HTTP ${response.status}`);
          error.retryable = [408, 429, 500, 502, 503, 504].includes(response.status);
          const retryAfter = response.headers.get('Retry-After');
          const seconds = Number(retryAfter);
          if (retryAfter) error.retryAfterMs = Number.isFinite(seconds)
            ? seconds * 1000 : Date.parse(retryAfter) - Date.now();
          throw error;
        }
        const data = await response.json();
        if (!Array.isArray(data) || data.some(cam => !cam || typeof cam.id !== 'string')) {
          const error = new Error('Invalid camera locations response');
          error.retryable = false;
          throw error;
        }
        if (stopped) return;
        onData(data);
        retries = 0;
        onState('ready');
        schedule(refreshMs);
      } catch (error) {
        if (stopped) return;
        // Network failures, interrupted bodies and request timeouts may recover.
        const retryable = error.retryable ?? (timedOut || error instanceof TypeError || error instanceof SyntaxError);
        if (retryable && retries < delays.length) {
          const backoff = delays[retries++] * (1 + Math.random() * 0.3);
          const requested = Number.isFinite(error.retryAfterMs) ? error.retryAfterMs : 0;
          onState('retrying');
          schedule(Math.max(backoff, Math.min(Math.max(requested, 0), 300000)));
        } else {
          retries = 0;
          onState('error');
          // Authentication/permission failures need user action, not repeated polling.
          if (retryable) schedule(refreshMs);
        }
      } finally {
        clearTimeout(timeout);
        if (active === controller) active = null;
      }
    }
    return {
      refresh,
      retry() { retries = 0; return refresh(); },
      stop() { stopped = true; clearTimeout(timer); active?.abort(); }
    };
  }
  global.BSnapMapData = { create };
})(window);
