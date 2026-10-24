// 施設・市区町村の検索（トップページ）。索引は最初に入力欄を触ったときに読み込む。
(function () {
  const box = document.getElementById('search');
  if (!box) return;
  const input = box.querySelector('input');
  const out = box.querySelector('.results');
  const status = box.querySelector('.status');
  const script = document.currentScript || document.querySelector('script[src$="search.js"]');
  const root = new URL('../', script.src);
  let idx = null, loading = null;

  const norm = s => (s || '').normalize('NFKC').toLowerCase().replace(/[\s　]/g, '');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let pending = 0;

  function load() {
    if (idx || loading) return loading;
    status.textContent = '読み込み中…';
    loading = fetch(new URL('assets/search.json', root)).then(r => r.json()).then(d => {
      const P = d.P, S = d.S;
      idx = [];
      d.m.forEach(([m, p]) => idx.push({
        kind: 'muni', title: m, sub: P[p] + '　障害福祉事業所の一覧',
        url: new URL(`p/${S[p]}/${encodeURIComponent(m)}/`, root).href, key: norm(P[p] + m),
      }));
      // 事業所の索引は分けてあるので、並行して読み込み、届いたものから検索に使う
      pending = d.F.length;
      d.F.forEach(f => fetch(new URL('assets/' + f, root)).then(r => r.json()).then(x => {
        x.f.forEach(([no, name, ci, p, mi, cs, alt]) => {
          const corp = x.C[ci], m = mi >= 0 ? d.m[mi][0] : '', svc = cs.map(i => d.N[i]).join('・');
          idx.push({
            kind: 'fac', title: name, sub: `${svc}｜${corp}｜${P[p]}${m}`, label: d.N[cs[0]],
            url: new URL(`f/${no}/`, root).href, key: norm(name + ' ' + (alt || '') + ' ' + corp + ' ' + P[p] + m + ' ' + svc + ' ' + no),
          });
        });
      }).catch(() => {}).finally(() => { pending--; run(); }));
      status.textContent = '';
      run();
    }).catch(() => { status.textContent = '検索の準備に失敗しました。ページを読み込み直してください。'; loading = null; });
    return loading;
  }

  function run() {
    const words = input.value.split(/[\s　]+/).map(norm).filter(Boolean);
    if (!words.length) { out.innerHTML = ''; status.textContent = ''; return; }
    if (!idx) { load(); return; }
    const hits = [];
    for (const e of idx) {
      if (words.every(w => e.key.includes(w))) {
        hits.push(e);
        if (hits.length >= 200) break;
      }
    }
    // 市区町村 → 施設名が先頭一致 → その他 の順に並べる
    const rank = e => (e.kind === 'muni' ? 0 : norm(e.title).startsWith(words[0]) ? 1 : 2);
    hits.sort((a, b) => rank(a) - rank(b));
    const shown = hits.slice(0, 40);
    const wait = pending > 0 ? '（事業所を読み込み中…）' : '';
    status.textContent = (hits.length ? `${hits.length >= 200 ? '200件以上' : hits.length + '件'}見つかりました${hits.length > 40 ? '（上位40件を表示）' : ''}` : pending > 0 ? '' : '見つかりませんでした。別の言葉で試してください。') + wait;
    out.innerHTML = shown.map(e => `<li><a href="${e.url}"><span class="k">${e.kind === 'muni' ? '地域' : esc(e.label)}</span><b>${esc(e.title)}</b><span class="s">${esc(e.sub)}</span></a></li>`).join('');
  }

  input.addEventListener('focus', load, { once: true });
  input.addEventListener('input', run);
  box.addEventListener('submit', e => { e.preventDefault(); run(); });
  if (location.hash === '#search') { input.focus(); }
})();
