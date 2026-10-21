// サービス別ページのランキング（都道府県・市区町村・法人・新規掲載・掲載終了）
(function () {
  const el = document.getElementById('rank-data');
  if (!el) return;
  const D = JSON.parse(el.textContent);
  const fmt = n => n.toLocaleString('ja-JP');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const delta = d => {
    const cls = d > 0 ? 'up' : d < 0 ? 'down' : 'zero';
    return `<span class="d ${cls}">${d > 0 ? '+' + fmt(d) : d < 0 ? '−' + fmt(-d) : '±0'}</span>`;
  };
  const link = (name, url) => url ? `<a href="${esc(url)}">${esc(name)}</a>` : esc(name);

  const $ = id => document.getElementById(id);
  const sel = $('rk-pref'), list = $('rk-list'), more = $('rk-more'), note = $('rk-note'), seg = $('rk-sort');
  D.P.forEach((p, i) => sel.insertAdjacentHTML('beforeend', `<option value="${i}">${p}</option>`));

  const TABS = ['pref', 'muni', 'corp', 'new', 'gone'];
  let tab = 'pref', sort = 'count', pref = -1, shown = 30;
  try { const h = location.hash.slice(1); if (TABS.includes(h)) tab = h; } catch (e) { /* noop */ }

  function rows() {
    if (tab === 'pref') return D.pref.map(r => ({ name: r[0], n: r[1], d: r[1] - r[2], url: r[3] }));
    if (tab === 'muni') return D.muni.filter(r => pref < 0 || r[1] === pref)
      .map(r => ({ name: D.P[r[1]] + ' ' + r[0], n: r[2], d: r[2] - r[3], url: r[4] }));
    if (tab === 'corp') return D.corp.map(r => ({ name: r[0], n: r[1], d: r[1] - r[2], fresh: r[2] === 0 }));
    const src = tab === 'new' ? D.new : D.gone;
    return src.filter(r => pref < 0 || r[3] === pref)
      .map(r => ({ list: true, name: r[0], sub: r[1], place: D.P[r[3]] + ' ' + r[2], url: r[4], fac: r[5] }));
  }

  const NOTES = {
    pref: '都道府県ごとの事業所数です。',
    muni: '事業所の所在地（市区町村）ごとの数です。市区町村名から、その地域の全サービスのページへ移れます。',
    corp: '5事業所以上を運営する法人、または半年で2件以上増減した法人です。',
    new: `${D.prev}になく、${D.cur}に掲載された事業所です。掲載の有無で判定しているため、実際の開設日とは一致しない場合があります。`,
    gone: `${D.prev}にあり、${D.cur}に掲載がない事業所です。廃止のほか、休止や番号の変更も含みます。`,
  };

  function render() {
    document.querySelectorAll('#rk-tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    const isList = tab === 'new' || tab === 'gone';
    seg.hidden = isList;
    sel.hidden = !(tab === 'muni' || isList);
    seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sort === sort)));
    let r = rows();
    if (!isList) {
      if (sort === 'count') r.sort((a, b) => b.n - a.n || b.d - a.d);
      if (sort === 'inc') r.sort((a, b) => b.d - a.d || b.n - a.n);
      if (sort === 'dec') r.sort((a, b) => a.d - b.d || b.n - a.n);
    } else r.sort((a, b) => a.place.localeCompare(b.place, 'ja'));
    note.textContent = NOTES[tab] + (isList ? `（${fmt(r.length)}件）` : '');
    const max = isList ? 1 : Math.max(1, ...r.map(x => x.n));
    const slice = r.slice(0, shown);
    list.innerHTML = slice.length ? slice.map((x, i) => isList
      ? `<li><span class="no">${i + 1}</span><span class="nm"><b>${link(x.name, x.fac)}</b><span class="sub">${esc(x.sub)}｜${link(x.place, x.url)}</span></span><span></span></li>`
      : `<li class="${i < 3 && sort !== 'dec' ? 'top' : ''}"><span class="no">${i + 1}</span><span class="nm"><b>${link(x.name, x.url)}${x.fresh ? '<span class="chip hot">新規参入</span>' : ''}</b><span class="bar"><i style="width:${(x.n / max * 100).toFixed(1)}%"></i></span></span><span class="val"><span class="n">${fmt(x.n)}</span>${delta(x.d)}</span></li>`
    ).join('') : '<li style="display:block;text-align:center;color:var(--muted)">該当する事業所はありません。</li>';
    more.hidden = r.length <= shown;
    more.textContent = `さらに表示（残り ${fmt(r.length - shown)}件）`;
  }

  $('rk-tabs').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    tab = b.dataset.tab; shown = 30; render();
  });
  seg.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    sort = b.dataset.sort; shown = 30; render();
  });
  sel.addEventListener('change', () => { pref = +sel.value; shown = 30; render(); });
  more.addEventListener('click', () => { shown += 50; render(); });
  render();
})();
