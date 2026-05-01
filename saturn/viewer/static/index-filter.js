// Progressive-enhancement filter + pager for the readings list.
// No JS = page still works (full list shows; pager hides via [hidden] attribute).
(() => {
  const list = document.getElementById('readings-list');
  if (!list) return;
  const filterInput = document.getElementById('readings-filter');
  const kindRadios = document.querySelectorAll('input[name="kind"]');
  const empty = document.getElementById('readings-empty');
  const countEl = document.getElementById('readings-count');
  const pager = document.querySelector('.readings-pager');
  const pagerPrev = document.getElementById('pager-prev');
  const pagerNext = document.getElementById('pager-next');
  const pagerInfo = document.getElementById('pager-info');
  const pageSize = parseInt(list.dataset.pageSize || '50', 10);

  const articles = Array.from(list.querySelectorAll('article.reading'));
  const total = articles.length;

  let state = {
    query: '',
    kind: 'all',
    page: 0,
  };

  const visibleArticles = () => {
    const q = state.query.toLowerCase().trim();
    return articles.filter((a) => {
      if (q) {
        const haystack = (
          (a.dataset.name || '') + ' ' +
          (a.dataset.source || '')
        ).toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (state.kind === 'all') return true;
      if (state.kind === 'reading') return a.dataset.hasInsight === '1';
      if (state.kind === 'notes') return a.dataset.hasNotes === '1';
      return a.dataset.kind === state.kind;
    });
  };

  const apply = () => {
    const matches = visibleArticles();

    // Hide everything first
    articles.forEach((a) => { a.hidden = true; });

    if (matches.length === 0) {
      empty.hidden = false;
      pager.hidden = true;
      countEl.textContent = `${String(total).padStart(3, '0')} files (0 visible)`;
      return;
    }
    empty.hidden = true;

    // Pagination
    const pages = Math.ceil(matches.length / pageSize);
    if (state.page >= pages) state.page = 0;
    const start = state.page * pageSize;
    const end = Math.min(start + pageSize, matches.length);

    matches.slice(start, end).forEach((a, i) => {
      a.hidden = false;
      const num = a.querySelector('.numeral-current');
      if (num) num.textContent = String(start + i + 1).padStart(3, '0');
    });

    if (pages > 1) {
      pager.hidden = false;
      pagerInfo.textContent = `page ${state.page + 1} of ${pages} · ${matches.length} of ${total}`;
      pagerPrev.disabled = state.page === 0;
      pagerNext.disabled = state.page >= pages - 1;
    } else {
      pager.hidden = true;
    }
    countEl.textContent = `${String(total).padStart(3, '0')} files (${matches.length} visible)`;
  };

  // Bind events
  let debounce;
  filterInput.addEventListener('input', (e) => {
    state.query = e.target.value;
    state.page = 0;
    clearTimeout(debounce);
    debounce = setTimeout(apply, 80);
  });
  kindRadios.forEach((r) => r.addEventListener('change', (e) => {
    state.kind = e.target.value;
    state.page = 0;
    apply();
  }));
  pagerPrev.addEventListener('click', () => {
    if (state.page > 0) { state.page -= 1; apply(); window.scrollTo({ top: list.offsetTop - 40 }); }
  });
  pagerNext.addEventListener('click', () => {
    state.page += 1; apply(); window.scrollTo({ top: list.offsetTop - 40 });
  });

  // Collection chips fill the filter with their prefix — quick way to
  // narrow 200 findings down to a topic.
  document.querySelectorAll('.collection-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const prefix = chip.dataset.collection || '';
      filterInput.value = prefix;
      state.query = prefix;
      state.page = 0;
      // Highlight the active chip
      document.querySelectorAll('.collection-chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      apply();
      filterInput.focus();
      window.scrollTo({ top: list.offsetTop - 40 });
    });
  });

  // Clearing the filter input also clears the active-chip highlight
  filterInput.addEventListener('input', () => {
    if (!filterInput.value) {
      document.querySelectorAll('.collection-chip').forEach((c) => c.classList.remove('active'));
    }
  });

  // Keyboard: '/' focuses the filter, Esc clears
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== filterInput) {
      e.preventDefault();
      filterInput.focus();
      filterInput.select();
    } else if (e.key === 'Escape' && document.activeElement === filterInput) {
      filterInput.value = '';
      state.query = '';
      state.page = 0;
      apply();
    }
  });

  apply();
})();
