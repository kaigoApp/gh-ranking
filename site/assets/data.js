// お仕事で使う方へのページ（地域のくらべ表・半年の動き）
(function () {
  const el = document.getElementById('data-data');
  if (!el) return;
  const D = JSON.parse(el.textContent);
  const fmt = n => n.toLocaleString('ja-JP');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const delta = d => {
    if (d === null) return '<span class="d zero">—</span>';
    const cls = d > 0 ? 'up' : d < 0 ? 'down' : 'zero';
    return `<span class="d ${cls}">${d > 0 ? '+' + fmt(d) : d < 0 ? '−' + fmt(-d) : '±0'}</span>`;
  };
  const link = (name, url) => url ? `<a href="${esc(url)}">${esc(name)}</a>` : esc(name);
  const $ = id => document.getElementById(id);
  const cache = {};
  const load = (dir, pi) => cache[dir + pi] || (cache[dir + pi] = fetch(`${dir}/${D.S[pi]}.json`).then(r => {
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }));

  // ---------- 地域のくらべ表
  const area = $('cp-area'), svc = $('cp-svc'), body = $('cp-body'), note = $('cp-note'), table = $('cp-table');
  let key = 'n', desc = true, rows = [];

  function toRow(name, url, v) {
    const [n, p6, p12, corp] = v || [0, 0, 0, 0];
    return {
      name, url, n: n || 0, corp: corp || 0,
      d6: p6 === null ? null : (n || 0) - p6,
      d12: p12 === null || !D.ya ? null : (n || 0) - p12,
      r12: p12 ? ((n || 0) - p12) / p12 * 100 : null,
    };
  }

  function draw() {
    table.querySelectorAll('th button').forEach(b => {
      const th = b.parentElement;
      th.setAttribute('aria-sort', b.dataset.k === key ? (desc ? 'descending' : 'ascending') : 'none');
    });
    const r = rows.slice();
    r.sort((a, b) => {
      if (key === 'name') return desc ? b.name.localeCompare(a.name, 'ja') : a.name.localeCompare(b.name, 'ja');
      const x = a[key], y = b[key];
      if (x === null && y === null) return b.n - a.n;
      if (x === null) return 1;
      if (y === null) return -1;
      return (desc ? y - x : x - y) || b.n - a.n;
    });
    body.innerHTML = r.length ? r.map(x => `<tr><td class="name">${link(x.name, x.url)}</td><td class="n">${fmt(x.n)}</td>`
      + `<td class="r">${delta(x.d6)}</td><td class="r">${delta(x.d12)}</td>`
      + `<td class="r tnum">${x.r12 === null ? '—' : (x.r12 > 0 ? '+' : '') + x.r12.toFixed(1) + '%'}</td><td class="r tnum">${fmt(x.corp)}</td></tr>`).join('')
      : '<tr><td colspan="6" style="text-align:center;color:var(--muted)">この地域には事業所がありません。</td></tr>';
  }

  function compare() {
    const pi = +area.value, c = svc.value;
    const sname = c === 'all' ? '全サービス（延べ）' : D.N[c];
    const span = `増減は${D.prev}・${D.ya || D.prev}との比較です。`;
    if (pi < 0) {
      rows = D.T[c].map((v, i) => toRow(D.P[i], D.pl[i], v));
      note.textContent = `全国の都道府県ごとの${sname}の事業所数です（${D.cur}時点）。${span}`;
      draw();
      return;
    }
    note.textContent = '読み込んでいます…';
    load('m', pi).then(ms => {
      if (+area.value !== pi || svc.value !== c) return;
      rows = ms.map(m => toRow(m[0], m[1], c === 'all' ? m[2] : m[3][c])).filter(x => x.n || x.d6 || x.d12);
      const none = ms.length - rows.length;
      note.textContent = `${D.P[pi]}の市区町村ごとの${sname}の事業所数です（${D.cur}時点）。${span}`
        + (c !== 'all' && none > 0 ? `このサービスの事業所がない市区町村が${fmt(none)}あります（表には出していません）。` : '');
      draw();
    }).catch(() => { note.textContent = '読み込めませんでした。時間をおいてもう一度お試しください。'; });
  }

  table.querySelector('thead').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    if (key === b.dataset.k) desc = !desc; else { key = b.dataset.k; desc = key !== 'name'; }
    draw();
  });
  area.addEventListener('change', compare);
  svc.addEventListener('change', compare);
  compare();

  // ---------- 半年の動き
  const mp = $('mv-pref'), ms = $('mv-svc'), kindSeg = $('mv-kind'), first = $('mv-first');
  const list = $('mv-list'), more = $('mv-more'), mnote = $('mv-note');
  let kind = 1, shown = 50, mv = null;

  function drawMoves() {
    kindSeg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.kind === kind)));
    if (!mv) { list.innerHTML = ''; more.hidden = true; return; }
    const c = ms.value;
    const r = mv.filter(x => x[0] === kind && (c === 'all' || x[1].includes(c)) && (!first.checked || (c === 'all' ? x[5].length : x[5].includes(c))));
    mnote.textContent = `${D.P[+mp.value]}で${kind ? '新しく載った' : '載らなくなった'}事業所：${fmt(r.length)}件`;
    const tag = kind ? '初めて' : 'すべてやめた';
    list.innerHTML = r.slice(0, shown).map(x => `<li><b>${link(x[3], x[6])}${x[5].length ? `<span class="chip${kind ? ' hot' : ''}">${tag}</span>` : ''}</b>`
      + `<span>${esc(x[1].map(k => D.N[k]).join('・'))}｜${link(x[4], x[7])}${x[2] ? '｜' + esc(x[2]) : ''}</span></li>`).join('')
      || '<li><span>該当する事業所はありません。</span></li>';
    more.hidden = r.length <= shown;
    more.textContent = `さらに表示（残り ${fmt(r.length - shown)}件）`;
  }

  function moves() {
    shown = 50;
    if (mp.value === '') { mv = null; mnote.textContent = '都道府県を選ぶと一覧が出ます。'; drawMoves(); return; }
    const pi = +mp.value;
    mnote.textContent = '読み込んでいます…';
    load('mv', pi).then(r => { if (+mp.value === pi) { mv = r; drawMoves(); } })
      .catch(() => { mnote.textContent = '読み込めませんでした。時間をおいてもう一度お試しください。'; });
  }

  mp.addEventListener('change', moves);
  ms.addEventListener('change', () => { shown = 50; drawMoves(); });
  first.addEventListener('change', () => { shown = 50; drawMoves(); });
  kindSeg.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    kind = +b.dataset.kind; shown = 50; drawMoves();
  });
  more.addEventListener('click', () => { shown += 100; drawMoves(); });

  // ---------- そのサービスが1か所もない市区町村
  const gs = $('gp-svc'), gpref = $('gp-pref'), glist = $('gp-list'), gnote = $('gp-note');
  function gaps() {
    const c = gs.value, pi = +gpref.value;
    if (!c) { glist.innerHTML = ''; gnote.textContent = 'サービスを選ぶと一覧が出ます。'; return; }
    const ps = pi < 0 ? D.P.map((_, i) => i) : [pi];
    gnote.textContent = '読み込んでいます…';
    Promise.all(ps.map(i => load('m', i))).then(all => {
      if (gs.value !== c || +gpref.value !== pi) return;
      let total = 0, have = 0;
      const html = all.map((ms, k) => {
        const live = ms.filter(m => m[1]);
        const none = live.filter(m => !m[3][c] || !m[3][c][0]);
        total += live.length; have += live.length - none.length;
        if (!none.length) return '';
        return `<div class="gp"><h3>${esc(D.P[ps[k]])}<span class="c">${fmt(none.length)} / ${fmt(live.length)}</span></h3>`
          + `<p>${none.map(m => link(m[0], m[1])).join('、')}</p></div>`;
      }).join('');
      gnote.textContent = `${D.N[c]}の事業所がない市区町村：${fmt(total - have)}（${pi < 0 ? '全国' : D.P[pi]}の${fmt(total)}市区町村のうち）`;
      glist.innerHTML = html || '<p class="note">すべての市区町村に事業所があります。</p>';
    }).catch(() => { gnote.textContent = '読み込めませんでした。時間をおいてもう一度お試しください。'; });
  }
  gs.addEventListener('change', gaps);
  gpref.addEventListener('change', gaps);
})();
