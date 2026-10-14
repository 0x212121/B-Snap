/* Historical Offline overlay, independent of the map's current-status filters. */
(() => {
  'use strict';
  function create(map) {
    const t = (en, id) => document.documentElement.lang === 'id' ? id : en;
    let data = null;
    let state = 'idle';
    let request = null;
    let requestVersion = 0;
    let failed = false;
    let heat = null;
    const heatOptions = {
      radius: 30, blur: 20, max: 1, maxZoom: 16, minOpacity: 0.25,
      gradient: { 0.2: '#2563eb', 0.45: '#06b6d4', 0.65: '#facc15', 1: '#ef4444' }
    };
    const control = L.control({ position: 'bottomleft' });
    control.onAdd = function() {
      const panel = L.DomUtil.create('div', 'map-heatmap-control');
      panel.innerHTML = `
        <label class="map-heatmap-toggle"><input type="checkbox" id="map-heatmap-toggle"><span id="map-heatmap-title"></span></label>
        <label for="map-heatmap-period" id="map-heatmap-period-label"></label>
        <select id="map-heatmap-period"><option value="24h"></option><option value="7d" selected></option><option value="30d"></option></select>
        <div id="map-heatmap-legend" hidden>
          <div class="map-heatmap-gradient"></div>
          <div class="map-heatmap-scale"><span id="map-heatmap-low"></span><span id="map-heatmap-high"></span></div>
          <p id="map-heatmap-weight"></p>
          <p id="map-heatmap-window"></p>
          <p id="map-heatmap-status" role="status" aria-live="polite"></p>
          <p id="map-heatmap-coverage"></p>
        </div>`;
      L.DomEvent.disableClickPropagation(panel);
      L.DomEvent.disableScrollPropagation(panel);
      return panel;
    };
    control.addTo(map);
    const panel = control.getContainer();
    const toggle = panel.querySelector('#map-heatmap-toggle');
    const period = panel.querySelector('#map-heatmap-period');
    const label = (id, value) => { panel.querySelector(`#${id}`).textContent = value; };
    const hours = seconds => new Intl.NumberFormat(BSnapDates.locale(), { maximumFractionDigits: 1 }).format(seconds / 3600);
    const zoneLabel = zone => ({ 'Asia/Jakarta': 'WIB', 'Asia/Makassar': 'WITA', 'Asia/Jayapura': 'WIT' }[zone] || zone);

    function updateCameraDetails(node) {
      node.hidden = !toggle.checked;
      if (!toggle.checked) return;
      const point = data?.points.find(item => String(item.camera_id) === node.dataset.heatmapCamera);
      if (state !== 'ready') {
        node.textContent = state === 'error' ? t('Downtime history unavailable.', 'Riwayat downtime tidak tersedia.') : t('Loading downtime history…', 'Memuat riwayat downtime…');
        return;
      }
      node.textContent = point
        ? t(`Recorded Offline: ${hours(point.downtime_seconds)} h in the selected period.`, `Offline tercatat: ${hours(point.downtime_seconds)} jam pada periode pilihan.`)
        : t('No Offline duration recorded in the selected period.', 'Tidak ada durasi Offline tercatat pada periode pilihan.');
    }

    function updateLabels() {
      label('map-heatmap-title', t('Issue heatmap', 'Heatmap gangguan'));
      label('map-heatmap-period-label', t('History period', 'Periode riwayat'));
      [...period.options].forEach(option => {
        option.textContent = { '24h': t('Last 24 hours', '24 jam terakhir'), '7d': t('Last 7 days', '7 hari terakhir'), '30d': t('Last 30 days', '30 hari terakhir') }[option.value];
      });
      panel.querySelector('#map-heatmap-legend').hidden = !toggle.checked;
      label('map-heatmap-low', t('Low', 'Rendah'));
      label('map-heatmap-high', t('High', 'Tinggi'));
      label('map-heatmap-weight', t('Weight: Offline duration / period. Color also depends on camera density and zoom. Current-status filters do not affect history. Maintenance and Standalone cameras are excluded.', 'Bobot: durasi Offline / periode. Warna juga dipengaruhi kepadatan kamera dan zoom. Filter status saat ini tidak memengaruhi riwayat. Kamera Maintenance dan Standalone dikecualikan.'));
      label('map-heatmap-window', data ? `${BSnapDates.format(data.start, { timeZone: data.timezone })} – ${BSnapDates.format(data.end, { timeZone: data.timezone })} (${zoneLabel(data.timezone)})` : '');
      const messages = {
        idle: '', loading: t('Loading history…', 'Memuat riwayat…'),
        error: t('History could not be loaded. Toggle again to retry.', 'Riwayat gagal dimuat. Aktifkan kembali untuk mencoba lagi.'),
        ready: data?.points.length
          ? t(`${data.points.length} cameras with recorded downtime.`, `${data.points.length} kamera dengan downtime tercatat.`)
          : t('No mapped cameras with recorded downtime.', 'Tidak ada kamera di peta dengan downtime tercatat.')
      };
      label('map-heatmap-status', messages[state]);
      label('map-heatmap-coverage', data?.incomplete_history_cameras
        ? t(`${data.incomplete_history_cameras} cameras have incomplete history; unobserved time is excluded. Locations use current camera coordinates.`, `${data.incomplete_history_cameras} kamera memiliki riwayat tidak lengkap; waktu tanpa data tidak dihitung. Lokasi memakai koordinat kamera saat ini.`)
        : t('Locations use current camera coordinates.', 'Lokasi memakai koordinat kamera saat ini.'));
      document.querySelectorAll('[data-heatmap-camera]').forEach(updateCameraDetails);
    }

    async function refresh() {
      if (!toggle.checked) return;
      request?.abort();
      request = new AbortController();
      const version = ++requestVersion;
      state = 'loading';
      data = null;
      if (heat && map.hasLayer(heat)) map.removeLayer(heat);
      updateLabels();
      try {
        if (typeof L.heatLayer !== 'function') throw new Error('Heatmap plugin unavailable');
        const response = await fetch(`/camera-heatmap?period=${encodeURIComponent(period.value)}`, {
          headers: { Accept: 'application/json' }, signal: request.signal
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (!Array.isArray(result.points)) throw new Error('Invalid heatmap response');
        if (version !== requestVersion || !toggle.checked) return;
        data = result;
        // Recreate after removal: Leaflet.heat 0.2 cannot redraw a detached layer.
        const points = data.points.filter(point => Number.isFinite(point.lat) && Number.isFinite(point.lng) && Number.isFinite(point.intensity) && point.intensity > 0)
          .map(point => [point.lat, point.lng, point.intensity]);
        heat = L.heatLayer(points, heatOptions).addTo(map);
        state = 'ready';
        failed = false;
      } catch (error) {
        if (error.name === 'AbortError' || version !== requestVersion || !toggle.checked) return;
        state = 'error';
        if (!failed) Toast.error(t('Could not load the issue heatmap.', 'Gagal memuat heatmap gangguan.'));
        failed = true;
      }
      updateLabels();
    }
    toggle.addEventListener('change', () => {
      if (toggle.checked) { refresh(); return; }
      ++requestVersion;
      request?.abort();
      if (heat && map.hasLayer(heat)) map.removeLayer(heat);
      state = 'idle';
      data = null;
      updateLabels();
    });
    period.addEventListener('change', refresh);
    window.addEventListener('bsnap:languagechange', updateLabels);
    updateLabels();
    return {
      refresh,
      attachDetails(container, cameraId) {
        let detail = container.querySelector('[data-heatmap-camera]');
        if (!detail) {
          detail = document.createElement('p');
          detail.className = 'map-heatmap-camera';
          detail.dataset.heatmapCamera = String(cameraId);
          container.appendChild(detail);
        }
        updateCameraDetails(detail);
      }
    };
  }
  window.BSnapMapHeatmap = { create };
})();
