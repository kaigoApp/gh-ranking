// 事業所の一覧を、入力した言葉（名前・住所・法人）でしぼりこむ
(function () {
  const input = document.getElementById('flt');
  if (!input) return;
  const list = document.querySelector(input.dataset.target);
  const note = document.getElementById('flt-note');
  const norm = s => (s || '').normalize('NFKC').toLowerCase().replace(/[\s　]/g, '');
  const items = [...list.querySelectorAll('li')].map(li => [li, norm(li.textContent)]);
  input.addEventListener('input', () => {
    const words = input.value.split(/[\s　]+/).map(norm).filter(Boolean);
    let n = 0;
    for (const [li, text] of items) {
      const hit = words.every(w => text.includes(w));
      li.hidden = !hit;
      if (hit) n++;
    }
    note.textContent = words.length ? `${n}件` : '';
  });
})();
