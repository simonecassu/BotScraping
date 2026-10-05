(function () {
  const missingEl = document.getElementById('missing-count');

  async function save(ids, wanted) {
    const r = await fetch('/api/wanted', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids, wanted})
    });
    const d = await r.json();
    if (d.ok && missingEl) missingEl.textContent = d.missing;
    document.querySelectorAll('.set').forEach(sec => {
      const n = sec.querySelectorAll('.card.wanted').length;
      const el = sec.querySelector('.set-missing');
      if (el) el.textContent = n;
    });
  }

  document.querySelectorAll('.card .want').forEach(cb => {
    cb.addEventListener('change', () => {
      const card = cb.closest('.card');
      card.classList.toggle('wanted', cb.checked);
      save([card.dataset.id], cb.checked);
    });
  });

  document.querySelectorAll('.set').forEach(sec => {
    const cards = () => Array.from(sec.querySelectorAll('.card'));
    sec.querySelectorAll('[data-bulk]').forEach(btn => {
      btn.addEventListener('click', e => {
        e.preventDefault();
        const mode = btn.dataset.bulk;
        const on = [], off = [];
        cards().forEach(c => {
          const cb = c.querySelector('.want');
          let v = mode === 'all' ? true : mode === 'none' ? false : !cb.checked;
          cb.checked = v; c.classList.toggle('wanted', v);
          (v ? on : off).push(c.dataset.id);
        });
        if (on.length) save(on, true);
        if (off.length) save(off, false);
      });
    });
    const sel = sec.querySelector('.bulk-rarity');
    sel.addEventListener('change', () => {
      if (!sel.value) return;
      const ids = [];
      cards().filter(c => c.dataset.rarity === sel.value).forEach(c => {
        c.querySelector('.want').checked = true; c.classList.add('wanted'); ids.push(c.dataset.id);
      });
      if (ids.length) save(ids, true);
      sel.value = '';
    });
  });

  const filter = document.getElementById('filter');
  const onlyMissing = document.getElementById('only-missing');
  function applyFilter() {
    const q = filter.value.trim().toLowerCase();
    const om = onlyMissing.checked;
    document.querySelectorAll('.card').forEach(c => {
      const ok = (!q || c.dataset.text.includes(q)) && (!om || c.classList.contains('wanted'));
      c.style.display = ok ? '' : 'none';
    });
  }
  filter.addEventListener('input', applyFilter);
  onlyMissing.addEventListener('change', applyFilter);
})();
