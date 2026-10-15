/* An authenticated live preview opens only after an explicit camera-image click. */
(() => {
  'use strict';
  const t = (en, id) => document.documentElement.lang === 'id' ? id : en;
  let stopActive = null;

  function updatePreviewLabels() {
    document.querySelectorAll('[data-map-live-preview]').forEach(button => {
      const title = t('Click to watch live', 'Klik untuk menonton live');
      button.title = title;
      button.setAttribute('aria-label', `${title}: ${button.dataset.mapLivePreview}`);
      button.querySelector('.map-live-preview-label').textContent = title;
    });
  }

  function open(camera) {
    stopActive?.();
    const content = document.createElement('div');
    content.className = 'map-live-player';
    const header = document.createElement('header');
    header.className = 'map-live-header';
    const identity = document.createElement('div');
    identity.className = 'map-live-identity';
    const title = document.createElement('h2');
    title.id = 'map-live-title';
    title.textContent = camera.hostname || t('Live camera', 'Kamera live');
    const address = document.createElement('span');
    address.className = 'map-live-address';
    address.textContent = camera.ip || '';
    address.hidden = !camera.ip;
    identity.append(title, address);
    const badge = document.createElement('span');
    badge.className = 'map-live-badge';
    badge.textContent = 'LIVE';
    badge.hidden = true;
    const actions = document.createElement('div');
    actions.className = 'map-live-actions';
    const screen = document.createElement('div');
    screen.className = 'map-live-screen';
    const image = document.createElement('img');
    image.alt = camera.hostname || '';
    const status = document.createElement('p');
    status.className = 'map-live-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const fullscreen = document.createElement('button');
    fullscreen.type = 'button';
    fullscreen.className = 'map-live-icon-button';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'map-live-icon-button';
    close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    close.addEventListener('click', () => Swal.close());
    actions.append(fullscreen, close);
    header.append(identity, badge, actions);
    const overlay = document.createElement('div');
    overlay.className = 'map-live-overlay';
    const message = document.createElement('p');
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'map-live-retry';
    retry.addEventListener('click', connect);
    overlay.append(message, retry);
    screen.append(image, overlay);
    const footer = document.createElement('footer');
    footer.className = 'map-live-footer';
    const resolution = document.createElement('span');
    resolution.className = 'map-live-resolution';
    footer.append(status, resolution);
    content.append(header, screen, footer);
    let state = 'loading';
    let watch = null;
    let renew = null;
    let startupTimeout = null;
    let policyTimer = null;
    let policyRequest = null;
    let closed = false;
    let returnFocus = document.activeElement;

    const update = () => {
      const isFullscreen = document.fullscreenElement === content;
      const fullscreenLabel = isFullscreen
        ? t('Exit fullscreen', 'Keluar fullscreen') : t('Fullscreen', 'Layar penuh');
      fullscreen.setAttribute('aria-label', fullscreenLabel);
      fullscreen.title = fullscreenLabel;
      fullscreen.innerHTML = isFullscreen
        ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3v6H3m12-6v6h6M3 15h6v6m12-6h-6v6"/></svg>'
        : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6"/></svg>';
      const closeLabel = t('Close live stream', 'Tutup live stream');
      close.setAttribute('aria-label', closeLabel);
      close.title = closeLabel;
      if (!camera.hostname) title.textContent = t('Live camera', 'Kamera live');
      const messages = {
        loading: t('Connecting to camera…', 'Menghubungkan ke kamera…'),
        ready: t('Video only · No audio', 'Video tanpa audio'),
        error: t('Disconnected', 'Tidak terhubung')
      };
      status.textContent = messages[state];
      content.dataset.state = state;
      badge.hidden = state !== 'ready';
      overlay.hidden = state === 'ready';
      image.style.visibility = state === 'ready' ? 'visible' : 'hidden';
      message.textContent = state === 'error' ? t('Stream unavailable', 'Stream tidak tersedia') : messages.loading;
      retry.hidden = state !== 'error';
      retry.textContent = t('Retry', 'Coba lagi');
      resolution.textContent = state === 'ready' ? `${image.naturalWidth} × ${image.naturalHeight}` : '';
    };
    function fail() {
      if (closed || state === 'error') return;
      state = 'error';
      clearInterval(watch);
      clearTimeout(renew);
      clearTimeout(startupTimeout);
      clearInterval(policyTimer);
      policyRequest?.abort();
      image.removeAttribute('src');
      update();
      Toast.error(t('Camera live stream unavailable.', 'Live stream kamera tidak tersedia.'));
    }
    function connect() {
      if (closed) return;
      state = 'loading';
      update();
      clearInterval(watch);
      clearTimeout(startupTimeout);
      image.src = `/maps/cameras/${encodeURIComponent(camera.id)}/live?v=${Date.now()}`;
      // MJPEG load events vary by browser; naturalWidth becomes available on frame one.
      watch = setInterval(() => {
        if (image.naturalWidth > 0) {
          state = 'ready';
          clearInterval(watch);
          clearTimeout(startupTimeout);
          update();
        }
      }, 250);
      startupTimeout = setTimeout(fail, 60000);
      // Renew a bounded stream through authentication instead of keeping it open forever.
      clearTimeout(renew);
      renew = setTimeout(connect, 150000);
      clearInterval(policyTimer);
      policyTimer = setInterval(checkPolicy, 5000);
    }
    function stop() {
      if (closed) return;
      closed = true;
      clearInterval(watch);
      clearTimeout(renew);
      clearTimeout(startupTimeout);
      clearInterval(policyTimer);
      policyRequest?.abort();
      image.onerror = null;
      image.removeAttribute('src');
      window.removeEventListener('bsnap:languagechange', update);
      window.removeEventListener('pagehide', stop);
      document.removeEventListener('fullscreenchange', update);
      if (document.fullscreenElement === content) document.exitFullscreen().catch(() => {});
      if (stopActive === stop) stopActive = null;
      if (returnFocus?.isConnected) returnFocus.focus();
      returnFocus = null;
    }
    image.onerror = fail;
    async function checkPolicy() {
      if (closed || policyRequest) return;
      policyRequest = new AbortController();
      try {
        const response = await fetch('/maps/live/status', { headers: { Accept: 'application/json' }, cache: 'no-store', signal: policyRequest.signal });
        if (!response.ok) { fail(); return; }
        const policy = await response.json();
        if (!policy.enabled) fail();
      } catch (error) {
        if (error.name !== 'AbortError' && !closed) fail();
      } finally {
        policyRequest = null;
      }
    }
    fullscreen.addEventListener('click', async () => {
      try {
        if (document.fullscreenElement === content) await document.exitFullscreen();
        else if (content.requestFullscreen) await content.requestFullscreen();
        else throw new Error('Fullscreen unavailable');
      } catch (_) {
        Toast.info(t('Fullscreen is unavailable in this browser.', 'Fullscreen tidak tersedia di browser ini.'));
      }
    });
    update();
    stopActive = stop;
    themedSwal({
      html: content,
      target: document.fullscreenElement || document.body,
      width: 'min(1120px, 96vw)',
      showConfirmButton: false,
      showCloseButton: false,
      showClass: { popup: '' },
      hideClass: { popup: '' },
      customClass: { popup: 'no-translate map-live-dialog' },
      didOpen: popup => {
        popup.setAttribute('aria-labelledby', title.id);
        window.addEventListener('bsnap:languagechange', update);
        window.addEventListener('pagehide', stop);
        document.addEventListener('fullscreenchange', update);
        connect();
      },
      willClose: stop,
      didDestroy: stop
    });
  }

  function preview(container, url, camera) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'map-live-preview';
    button.dataset.mapLivePreview = camera.hostname || '';
    const image = document.createElement('img');
    image.src = typeof url === 'string' && url.trim() ? url : '/static/placeholder.svg';
    image.alt = camera.hostname || t('Camera snapshot', 'Snapshot kamera');
    image.onerror = () => {
      image.onerror = null;
      image.src = '/static/placeholder.svg';
    };
    const label = document.createElement('span');
    label.className = 'map-live-preview-label';
    button.append(image, label);
    button.addEventListener('click', () => open(camera));
    container.replaceChildren(button);
    updatePreviewLabels();
  }
  window.addEventListener('bsnap:languagechange', updatePreviewLabels);
  window.BSnapMapLive = { preview, open };
})();
