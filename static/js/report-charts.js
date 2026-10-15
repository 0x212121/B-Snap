/* Localize canvas content before display and PDF capture. Keep raw dates for sorting. */
(() => {
  'use strict';
  const originals = new WeakMap();
  const labels = {
    'API Calls': 'Panggilan API',
    'Commands Executed': 'Perintah Dieksekusi',
    'Emails Sent': 'Email Terkirim',
    'Availability (%)': 'Ketersediaan (%)',
    'Downtime (hours)': 'Durasi Offline (jam)',
    'Offline transitions': 'Transisi offline',
  };
  function localize(chart) {
    if (!chart) return;
    if (!originals.has(chart)) {
      originals.set(chart, {
        labels: [...chart.data.labels],
        datasets: chart.data.datasets.map(dataset => dataset.label),
        axes: Object.fromEntries(Object.entries(chart.options.scales || {})
          .map(([key, axis]) => [key, axis.title?.text])),
      });
    }
    const source = originals.get(chart);
    const language = document.documentElement.lang === 'id' ? 'id' : 'en';
    const translate = text => language === 'id' ? (labels[text] || text) : text;
    chart.options.locale = BSnapDates.locale();
    chart.data.labels = source.labels.map(value => /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? BSnapDates.format(value, { hour: undefined, minute: undefined, second: undefined }) : value);
    chart.data.datasets.forEach((dataset, index) => { dataset.label = translate(source.datasets[index]); });
    Object.entries(source.axes).forEach(([key, text]) => {
      if (text) chart.options.scales[key].title.text = translate(text);
    });
    chart.update('none');
  }
  window.BSnapReportCharts = { localize };
})();
