/* Shared snapshot/video loading. Pagination advances only after a valid response. */
(function (global) {
  'use strict';

  function create(options) {
    const { state, grid, sentinel, selector, pageSize } = options;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'ui-button ui-button-secondary hidden';
    retry.textContent = 'Retry';
    retry.hidden = true;
    sentinel.after(retry);
    let controller;
    let failedAppend = false;
    let paused = false;

    function updateSentinel() {
      const visible = state.hasMore && !paused;
      sentinel.classList.toggle('hidden', visible === false);
      sentinel.classList.toggle('flex', visible);
    }

    function checkPosition() {
      if (paused || state.isLoading || !state.hasMore) return;
      const bounds = sentinel.getBoundingClientRect();
      if (bounds.bottom >= -200 && bounds.top <= global.innerHeight + 200) load(true);
    }

    async function load(append = false) {
      if (append && (paused || state.isLoading || !state.hasMore)) return;
      controller?.abort();
      controller = new AbortController();
      const requestController = controller;
      const requestId = ++state.lastRequestId;
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        requestController.abort();
      }, 30000);
      const page = append ? state.currentPage + 1 : 1;
      const offset = append ? state.offset : 0;
      const params = options.params();
      if (options.pagination === 'page') params.set('page', page);
      else {
        params.set('offset', offset);
        params.set('limit', pageSize);
      }
      state.isLoading = true;
      paused = false;
      retry.hidden = true;
      retry.classList.toggle('hidden', true);
      updateSentinel();
      grid.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(`${options.url}?${params}`, {
          headers: { Accept: 'application/json' },
          signal: requestController.signal
        });
        if (!response.ok) throw new Error(`Gallery request failed (${response.status})`);
        const data = await response.json();
        if (!data || typeof data.html !== 'string' ||
            (options.validate && !options.validate(data))) {
          throw new Error('Invalid gallery response');
        }
        if (requestId !== state.lastRequestId) return;
        const container = document.createElement('div');
        container.innerHTML = data.html;
        const items = Array.from(container.querySelectorAll(selector));
        if (!append) {
          grid.innerHTML = data.html;
          options.onReplace?.(data);
        } else {
          // Offset/page counts follow the server slice, even when live inserts overlap it.
          const existing = new Set(Array.from(grid.querySelectorAll(selector), item =>
            item.dataset.id || item.dataset.videoId));
          const fragment = document.createDocumentFragment();
          items.forEach(item => {
            const id = item.dataset.id || item.dataset.videoId;
            if (!existing.has(id)) {
              existing.add(id);
              fragment.appendChild(item);
            }
          });
          grid.appendChild(fragment);
        }
        state.currentPage = page;
        state.offset = offset + items.length;
        state.hasMore = items.length === pageSize;
        options.onLoaded?.(items.length, append);
      } catch (error) {
        if (requestId !== state.lastRequestId || (error.name === 'AbortError' && !timedOut)) return;
        paused = true;
        failedAppend = append;
        retry.hidden = false;
        retry.classList.toggle('hidden', false);
        Toast.error(options.errorMessage, { title: 'Error' });
      } finally {
        clearTimeout(timeout);
        if (requestId === state.lastRequestId) {
          state.isLoading = false;
          grid.setAttribute('aria-busy', 'false');
          updateSentinel();
          // IntersectionObserver does not fire again if the sentinel stays visible.
          if (!paused) global.requestAnimationFrame(checkPosition);
        }
      }
    }

    retry.addEventListener('click', () => {
      paused = false;
      load(failedAppend);
    });
    state.observer = new IntersectionObserver(checkPosition, { rootMargin: '200px' });
    state.observer.observe(sentinel);
    updateSentinel();

    // Capture handles lazy images in both server-rendered and appended cards.
    grid.addEventListener('error', event => {
      const img = event.target;
      if (img.tagName !== 'IMG' || img.dataset.fallback) return;
      const attempts = Number(img.dataset.retries || 0);
      if (attempts < 2) {
        img.dataset.retries = String(attempts + 1);
        const url = new URL(img.src, global.location.href);
        url.searchParams.set('_retry', String(attempts + 1));
        setTimeout(() => { if (img.isConnected) img.src = url.href; }, 500 * (attempts + 1));
      } else {
        img.dataset.fallback = 'true';
        img.src = options.placeholder;
      }
    }, true);
    // An initial SSR thumbnail may have failed before this script attached.
    grid.querySelectorAll('img').forEach(img => {
      if (img.complete && !img.naturalWidth) img.dispatchEvent(new Event('error'));
    });

    return {
      reset() {
        paused = false;
        state.hasMore = true;
        state.offset = 0;
        state.currentPage = 0;
        return load(false);
      },
      loadMore() { return load(true); },
      checkPosition
    };
  }

  global.BSnapGallery = { create };
})(window);
