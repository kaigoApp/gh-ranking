// 近くの事業所を近い順に出す
(function () {
  const el = document.getElementById('near-data');
  if (!el) return;
  const D = JSON.parse(el.textContent);
  const fmt = n => n.toLocaleString('ja-JP');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const link = (name, url) => url ? `<a href="${esc(url)}">${esc(name)}</a>` : esc(name);
  const $ = id => document.getElementById(id);
  const where = $('nr-where'), note = $('nr-note'), list = $('nr-list'), more = $('nr-more'), svc = $('nr-svc');
  const mpref = $('nr-pref'), mmuni = $('nr-muni');
  const cache = {};
  let here = null, rows = [], shown = 30;

  const have = new Set(D.T);  // 事業所のある区画だけを読み込む（海の区画などは無い）
  const tile = (a, b) => {
    const k = a + '_' + b;
    if (!have.has(k)) return Promise.resolve([]);
    return cache[k] || (cache[k] = fetch(`t/${k}.json`).then(r => r.ok ? r.json() : []).catch(() => []));
  };
  function dist(la1, ln1, la2, ln2) {
    const R = 6371, r = Math.PI / 180;
    const x = Math.sin((la2 - la1) * r / 2) ** 2 + Math.cos(la1 * r) * Math.cos(la2 * r) * Math.sin((ln2 - ln1) * r / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  }

  function draw() {
    if (!here) return;
    const c = svc.value;
    const r = rows.filter(x => c === 'all' || x[3].includes(c));
    note.textContent = `${here.label}から近い順：${fmt(r.length)}か所`;
    list.innerHTML = r.slice(0, shown).map(x => {
      const km = x.d < 1 ? `${Math.round(x.d * 1000)}m` : `${x.d.toFixed(1)}km`;
      const map = `https://www.google.com/maps/dir/?api=1&origin=${here.lat},${here.lng}&destination=${x[0]},${x[1]}`;
      return `<li><b>${link(x[2], x[6])}<span class="chip">${km}</span></b>`
        + `<span>${esc(x[3].map(k => D.N[k]).join('・'))}｜${link(x[4], x[7])}｜${esc(x[5])}</span>`
        + `<span><a href="${esc(map)}" rel="noopener" target="_blank">道順</a>${x[8] ? `　<a href="tel:${esc(x[8].replace(/[^0-9+]/g, ''))}">${esc(x[8])}</a>` : ''}</span></li>`;
    }).join('') || '<li><span>近くに該当する事業所はありません。</span></li>';
    more.hidden = r.length <= shown;
    more.textContent = `さらに表示（残り ${fmt(r.length - shown)}か所）`;
  }

  function go(lat, lng, label) {
    here = { lat, lng, label };
    where.textContent = `${label}（緯度 ${lat.toFixed(4)}、経度 ${lng.toFixed(4)}）から探しています。`;
    note.textContent = '読み込んでいます…';
    const a = Math.floor(lat * 2), b = Math.floor(lng * 2), ps = [];
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) ps.push(tile(a + i, b + j));
    Promise.all(ps).then(ts => {
      if (here.lat !== lat || here.lng !== lng) return;
      rows = ts.flat().map(x => { const y = x.slice(); y.d = dist(lat, lng, x[0], x[1]); return y; })
        .filter(x => x.d <= 40).sort((p, q) => p.d - q.d);
      shown = 30;
      draw();
    });
  }

  mpref.addEventListener('change', () => {
    const pi = mpref.value === '' ? -1 : +mpref.value;
    mmuni.innerHTML = '<option value="">市区町村</option>' + D.M.map((m, i) => m[0] === pi ? `<option value="${i}">${esc(m[1])}</option>` : '').join('');
    mmuni.disabled = pi < 0;
  });
  mmuni.addEventListener('change', () => {
    if (mmuni.value === '') return;
    const m = D.M[+mmuni.value];
    go(m[2], m[3], `${D.P[m[0]]}${m[1]}の中心あたり`);
  });

  $('nr-addr').addEventListener('submit', e => {
    e.preventDefault();
    const q = $('nr-q').value.trim();
    if (!q) return;
    where.textContent = '住所を調べています…';
    // 国土地理院の住所検索（住所だけを送ります）
    fetch('https://msearch.gsi.go.jp/address-search/AddressSearch?q=' + encodeURIComponent(q))
      .then(r => r.json())
      .then(js => {
        if (!js.length) { where.textContent = 'その住所が見つかりませんでした。市区町村から選んでください。'; return; }
        const [lng, lat] = js[0].geometry.coordinates;
        go(lat, lng, js[0].properties.title);
      })
      .catch(() => { where.textContent = '住所を調べられませんでした。市区町村から選んでください。'; });
  });

  $('nr-geo').addEventListener('click', () => {
    if (!navigator.geolocation) { where.textContent = 'この端末では現在地を使えません。'; return; }
    where.textContent = '現在地を調べています…';
    navigator.geolocation.getCurrentPosition(
      p => go(p.coords.latitude, p.coords.longitude, 'いまいる場所'),
      () => { where.textContent = '現在地を使えませんでした。住所か市区町村で探してください。'; },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 });
  });

  svc.addEventListener('change', () => { shown = 30; draw(); });
  more.addEventListener('click', () => { shown += 50; draw(); });

  // ほかのページから ?s=サービス&p=都道府県&m=市区町村 で開かれたときは、その条件で始める
  const q = new URLSearchParams(location.search);
  if ([...svc.options].some(o => o.value === q.get('s'))) svc.value = q.get('s');
  if (q.get('p') && q.get('m')) {
    const i = D.M.findIndex(m => String(m[0]) === q.get('p') && m[1] === q.get('m'));
    if (i >= 0) {
      mpref.value = q.get('p');
      mpref.dispatchEvent(new Event('change'));
      mmuni.value = String(i);
      mmuni.dispatchEvent(new Event('change'));
    }
  }
})();
