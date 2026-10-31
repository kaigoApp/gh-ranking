// 事業所の一覧を、入力した言葉（名前・住所・法人）や条件（土曜・日曜・時間・新しい所）でしぼりこみ、並べかえる
(function () {
  const input = document.getElementById('flt');
  if (!input) return;
  const list = document.querySelector(input.dataset.target);
  const note = document.getElementById('flt-note');
  const tools = document.getElementById('sort-tools');
  const sortBy = document.getElementById('sort-by');
  const norm = s => (s || '').normalize('NFKC').toLowerCase().replace(/[\s　]/g, '');
  const lis = [...list.querySelectorAll(':scope > li')];
  const items = lis.map((li, i) => ({ li, i, text: norm(li.textContent), d: li.dataset }));
  const only = new Set();
  const count = list.closest('details') && list.closest('details').querySelector('summary .c');
  const total = count ? count.textContent : '';
  const TEST = {
    sat: d => d.sat === '1',
    sun: d => d.sun === '1',
    late: d => +d.end >= 17 * 60,
    new: d => d.new === '1',
  };

  // 中身のない条件は出さない（例：その地域で定休日の情報がほとんどない）
  if (tools) {
    let any = false;
    tools.querySelectorAll('[data-only]').forEach(b => {
      const k = b.dataset.only;
      const n = items.filter(x => TEST[k](x.d)).length;
      if (!n) b.hidden = true; else any = true;
    });
    if (sortBy) {
      [...sortBy.options].forEach(o => {
        if (o.value && !items.some(x => x.d[o.value])) o.remove();
      });
      if (sortBy.options.length > 1) any = true; else sortBy.closest('label').hidden = true;
    }
    tools.hidden = !any;
  }

  function apply() {
    const words = input.value.split(/[\s　]+/).map(norm).filter(Boolean);
    let n = 0;
    for (const x of items) {
      const hit = words.every(w => x.text.includes(w)) && [...only].every(k => TEST[k](x.d));
      x.li.hidden = !hit;
      if (hit) n++;
    }
    const key = sortBy ? sortBy.value : '';
    const sorted = key
      ? [...items].sort((a, b) => (+b.d[key] || -1) - (+a.d[key] || -1) || a.i - b.i)
      : items;
    sorted.forEach(x => list.appendChild(x.li));
    const filtered = words.length || only.size;
    if (count) count.textContent = filtered ? `${n}件 / ${total}` : total;
    note.textContent = filtered ? `${n}件` : '';
    if (filtered && only.size) note.textContent += '（定休日や時間の情報がない事業所は出ません）';
  }

  input.addEventListener('input', apply);
  if (tools) {
    tools.addEventListener('click', e => {
      const b = e.target.closest('[data-only]');
      if (!b) return;
      const k = b.dataset.only;
      if (only.has(k)) only.delete(k); else only.add(k);
      b.setAttribute('aria-pressed', only.has(k));
      apply();
      if (window.gtag) gtag('event', 'list_filter', { filter: k });
    });
  }
  if (sortBy) sortBy.addEventListener('change', () => {
    apply();
    if (window.gtag && sortBy.value) gtag('event', 'list_sort', { sort: sortBy.value });
  });
})();
