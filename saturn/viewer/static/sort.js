// Progressive-enhancement sort for the profile view's schema table.
// If JS fails or is disabled, the table still renders and is usable.
(() => {
  const tbody = document.getElementById('columns-tbody');
  if (!tbody) return;

  const COLUMN_INDEX = { column: 0, kind: 1, null_rate: 2, n_unique: 3 };
  const NUMERIC = new Set(['null_rate', 'n_unique']);

  const directions = new Map();

  document.querySelectorAll('button[data-sort]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.sort;
      const idx = COLUMN_INDEX[key];
      if (idx === undefined) return;

      const asc = !(directions.get(key) === 'asc');
      directions.set(key, asc ? 'asc' : 'desc');

      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {
        const cellA = a.cells[idx];
        const cellB = b.cells[idx];
        if (NUMERIC.has(key)) {
          const na = parseFloat(cellA.dataset.value ?? cellA.textContent);
          const nb = parseFloat(cellB.dataset.value ?? cellB.textContent);
          return asc ? na - nb : nb - na;
        }
        const sa = cellA.textContent.trim();
        const sb = cellB.textContent.trim();
        return asc ? sa.localeCompare(sb) : sb.localeCompare(sa);
      });
      rows.forEach((r) => tbody.appendChild(r));
      btn.setAttribute('aria-pressed', String(asc));
    });
  });
})();
