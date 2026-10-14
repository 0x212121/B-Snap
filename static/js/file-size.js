/* File sizes use KB below 1024 KB, and MB from 1024 KB onwards. Input is bytes. */
(() => {
  'use strict';

  function format(bytes) {
    const value = Number(bytes);
    const size = Number.isFinite(value) && value > 0 ? value : 0;
    return size < 1024 * 1024
      ? `${(size / 1024).toFixed(2)} KB`
      : `${(size / 1024 / 1024).toFixed(2)} MB`;
  }

  window.BSnapSizes = { format };
})();
