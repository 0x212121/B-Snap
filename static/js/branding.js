(() => {
  'use strict';
  const form = document.getElementById('branding-form');
  if (!form) return;
  const data = JSON.parse(document.getElementById('branding-data').textContent);
  const field = name => form.elements.namedItem(name);
  const upload = document.getElementById('branding-upload');
  const preview = document.getElementById('branding-preview');
  const save = document.getElementById('branding-save');
  const revert = document.getElementById('branding-revert');
  const defaults = document.getElementById('branding-defaults');
  const cancelUpload = document.getElementById('branding-clear-upload');
  const filename = document.getElementById('branding-filename');
  const status = document.getElementById('branding-status');
  const fields = document.getElementById('branding-fields');
  const t = value => window.BSnapI18n?.t(value) || value;
  let saving = false;
  let objectUrl = null;

  function snapshot() {
    return {name: field('name').value.trim(), tagline: field('tagline').value.trim(),
      logo: field('logo').value, palette: field('palette').value};
  }

  function dirty() {
    const current = snapshot();
    return Boolean(upload.files[0]) || Object.keys(current).some(key => current[key] !== data.initial[key]);
  }

  function releasePreview() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  }

  function clearUpload() {
    upload.value = '';
    releasePreview();
  }

  function render() {
    const current = snapshot();
    const colors = data.palettes[current.palette] || data.palettes.blue;
    preview.style.setProperty('--ui-primary', colors.primary);
    preview.style.setProperty('--ui-primary-hover', colors.hover);
    document.querySelectorAll('[data-branding-preview-name]').forEach(node => {
      node.textContent = current.name || data.defaults.name;
    });
    const tagline = document.getElementById('branding-preview-tagline');
    tagline.textContent = current.tagline;
    tagline.hidden = !current.tagline;
    document.querySelectorAll('[data-branding-preview-logo]').forEach(node => {
      node.src = objectUrl || current.logo;
      node.alt = current.name || data.defaults.name;
    });
    filename.textContent = upload.files[0]?.name || '';
    filename.hidden = !upload.files[0];
    cancelUpload.hidden = !upload.files[0];
    const changed = dirty();
    status.textContent = t(saving ? 'Saving branding...' : changed ? 'Unsaved changes.' : 'No changes to save.');
    status.classList.add('no-translate');
    save.disabled = saving || !changed;
    revert.disabled = saving || !changed;
    defaults.disabled = saving;
    save.querySelector('span').textContent = t(saving ? 'Saving branding...' : 'Save branding');
  }

  function setDraft(value) {
    clearUpload();
    for (const key of ['name', 'tagline', 'logo', 'palette']) field(key).value = value[key];
    render();
  }

  form.addEventListener('input', render);
  field('logo').addEventListener('change', () => { clearUpload(); render(); });
  document.getElementById('branding-upload-button').addEventListener('click', () => upload.click());
  cancelUpload.addEventListener('click', () => { clearUpload(); render(); });
  revert.addEventListener('click', () => setDraft(data.initial));
  defaults.addEventListener('click', () => setDraft(data.defaults));
  upload.addEventListener('change', () => {
    releasePreview();
    const file = upload.files[0];
    if (file) {
      if (!/\.(png|ico)$/i.test(file.name) || file.size > data.maxLogoSize) {
        clearUpload();
        Toast.error(t('Choose a PNG or ICO file up to 512 KB.'));
      } else {
        objectUrl = URL.createObjectURL(file);
      }
    }
    render();
  });

  function setPreviewMode(mode) {
    preview.dataset.mode = mode;
    document.querySelectorAll('[data-preview-mode]').forEach(button => {
      const selected = button.dataset.previewMode === mode;
      button.setAttribute('aria-pressed', String(selected));
      button.classList.toggle('ui-button-primary', selected);
      button.classList.toggle('ui-button-secondary', !selected);
    });
  }
  document.querySelectorAll('[data-preview-mode]').forEach(button => {
    button.addEventListener('click', () => setPreviewMode(button.dataset.previewMode));
  });
  setPreviewMode(document.documentElement.classList.contains('dark') ? 'dark' : 'light');
  window.addEventListener('bsnap:languagechange', render);
  window.addEventListener('beforeunload', event => {
    if (!saving && dirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pagehide', releasePreview);

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || !dirty() || !form.reportValidity()) return;
    // Capture enabled controls before locking the form during the request.
    const payload = new FormData(form);
    saving = true;
    fields.disabled = true;
    form.setAttribute('aria-busy', 'true');
    render();
    try {
      const response = await fetch('/admin/branding', {
        method: 'POST', headers: {'Accept': 'application/json'}, body: payload
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = Array.isArray(result.detail) ? result.detail.map(item => item.msg).join('; ') : result.detail;
        throw new Error(result.message || detail || t('Unable to save branding.'));
      }
      try { sessionStorage.setItem('bsnap.branding.saved', '1'); }
      catch (_) { Toast.success(t('Branding saved.')); }
      window.location.reload();
    } catch (error) {
      Toast.error(error instanceof TypeError ? t('Network error. Please try again.') : t(error.message || 'Unable to save branding.'));
      saving = false;
      fields.disabled = false;
      form.removeAttribute('aria-busy');
      render();
    }
  });
  try {
    if (sessionStorage.getItem('bsnap.branding.saved')) {
      sessionStorage.removeItem('bsnap.branding.saved');
      Toast.success(t('Branding saved.'));
    }
  } catch (_) { /* Storage can be unavailable; saving still works. */ }
  render();
})();
