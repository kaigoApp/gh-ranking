// ★で事業所を保存し、/saved/ で並べて比べる・印刷する・送る。
// 保存はこの端末のブラウザの中（localStorage）だけ。ジモふくには何も送らない。
(function () {
  const KEY = 'jf-saved';
  const MAX = 30;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const NO_RE = /^[0-9A-Za-z]{1,20}$/;

  function read() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || '[]');
      return Array.isArray(v) ? v.filter(x => x && NO_RE.test(x.no)) : [];
    } catch (e) { return []; }
  }
  function write(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); return true; } catch (e) { return false; }
  }
  const has = no => read().some(x => x.no === no);

  // サイトのいちばん上（/ や /gh-ranking/）。saved.js の読み込み先から求める
  const me = document.currentScript || document.querySelector('script[src*="assets/saved.js"]');
  const ROOT = me ? new URL('../', me.src) : new URL('/', location.href);
  const facUrl = no => new URL(`f/${no}/`, ROOT).href;

  function toggle(no, name) {
    let list = read();
    if (list.some(x => x.no === no)) {
      list = list.filter(x => x.no !== no);
    } else {
      if (list.length >= MAX) { alertBox(`保存できるのは${MAX}か所までです。いらないものを消してください。`); return; }
      list.push({ no, name, at: Date.now() });
    }
    if (!write(list)) alertBox('このブラウザでは保存できません（プライベートモードなど）。');
    paint();
  }

  function alertBox(msg) {
    const fab = document.getElementById('saved-fab');
    let t = document.getElementById('saved-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'saved-toast'; t.className = 'saved-toast noprint'; t.setAttribute('role', 'status');
      document.body.appendChild(t);
    }
    t.textContent = msg; t.hidden = false;
    clearTimeout(alertBox.tm);
    alertBox.tm = setTimeout(() => { t.hidden = true; }, 3500);
    if (fab) fab.classList.add('bump');
  }

  // ☆ボタンと、右下の「保存した事業所」の表示をそろえる
  function paint() {
    const list = read();
    const set = new Set(list.map(x => x.no));
    document.querySelectorAll('[data-fav]').forEach(b => {
      const on = set.has(b.dataset.fav);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (b.classList.contains('fav-s')) {
        b.textContent = on ? '★' : '☆';
        b.setAttribute('aria-label', `${b.dataset.name}を${on ? '保存から外す' : '保存する'}`);
      } else {
        const i = b.querySelector('i'), em = b.querySelector('em');
        if (i) i.textContent = on ? '★' : '☆';
        if (em) em.textContent = on ? '保存済み' : '保存する';
      }
    });
    const fab = document.getElementById('saved-fab'), n = document.getElementById('saved-n');
    if (fab && n) {
      n.textContent = list.length;
      fab.hidden = !list.length || !!document.getElementById('sv-main');
    }
  }

  document.addEventListener('click', e => {
    const b = e.target.closest('[data-fav]');
    if (!b) return;
    e.preventDefault();
    const was = has(b.dataset.fav);
    toggle(b.dataset.fav, b.dataset.name || '');
    if (!was && has(b.dataset.fav)) alertBox(`「${b.dataset.name}」を保存しました。右下の「保存した事業所」から見られます。`);
    if (window.gtag) gtag('event', was ? 'fav_remove' : 'fav_add', { fav_from: location.pathname.split('/')[1] || 'top' });
  });
  window.addEventListener('storage', e => { if (e.key === KEY) paint(); });
  document.addEventListener('DOMContentLoaded', () => { paint(); initSaved(); });

  // ---------------------------------------------------------------- /saved/ のページ
  function initSaved() {
    const main = document.getElementById('sv-main');
    if (!main) return;
    const $ = id => document.getElementById(id);
    const params = new URLSearchParams(location.search);
    const shared = (params.get('n') || '').split(/[.,]/).filter(x => NO_RE.test(x)).slice(0, MAX);
    const isShared = shared.length > 0;
    let view = 'list';
    let recs = {};      // 事業所番号 → 事業所ページから読み取った中身
    let map = null, layer = null, bounds = null;
    // 印刷のときは地図の幅が変わるので、描き直して全部の印が入るようにする
    const refit = () => { if (map) { map.invalidateSize(false); if (bounds) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15, animate: false }); } };
    // 紙の幅（A4 の本文に収まる 170mm）にそろえてから描き直す
    window.addEventListener('beforeprint', () => { $('sv-map').style.width = '170mm'; refit(); });
    window.addEventListener('afterprint', () => { $('sv-map').style.width = ''; refit(); });

    const items = () => isShared ? shared.map(no => ({ no })) : read();
    const d = new Date();
    $('sv-date').textContent = `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;

    function cache(no) { try { return JSON.parse(localStorage.getItem('jf-rec-' + no) || 'null'); } catch (e) { return null; } }
    function load(no) {
      return fetch(facUrl(no)).then(r => r.ok ? r.text() : '').then(t => {
        const m = t.match(/<script type="application\/json" id="fav-data">([\s\S]*?)<\/script>/);
        if (!m) return null;
        const rec = JSON.parse(m[1]);
        try { localStorage.setItem('jf-rec-' + no, JSON.stringify(rec)); } catch (e) { /* 入らなくても表示はする */ }
        return rec;
      }).catch(() => null);
    }

    function memoOf(no) { const x = read().find(y => y.no === no); return x && x.memo || ''; }
    function setMemo(no, v) {
      const list = read(); const x = list.find(y => y.no === no);
      if (x) { x.memo = v.slice(0, 500); write(list); }
    }

    function shareUrl() {
      const u = new URL('saved/', ROOT);
      u.search = '?n=' + items().map(x => x.no).join('.');
      return u.href;
    }

    function row(label, v) { return v ? `<div><dt>${label}</dt><dd>${v}</dd></div>` : ''; }

    function render() {
      const list = items();
      $('sv-empty').hidden = list.length > 0;
      main.hidden = list.length === 0;
      $('sv-shared').hidden = !isShared;
      if (!list.length) return;
      const ol = $('sv-list');
      ol.innerHTML = list.map((x, i) => {
        const r = recs[x.no] || cache(x.no) || { no: x.no, name: x.name || '' };
        const tel = r.tel ? `<a href="tel:${esc(r.tel.replace(/[^0-9+]/g, ''))}" class="tnum">${esc(r.tel)}</a>` : '';
        const memo = isShared ? '' : memoOf(x.no);
        return `<li data-no="${esc(x.no)}">
  <div class="sv-h"><span class="sv-num">${i + 1}</span><div><a class="sv-name" href="${esc(facUrl(x.no))}">${esc(r.name || '（読みこみ中）')}</a>
  <span class="sv-svc">${esc((r.svc || []).join('・'))}</span></div>
  ${isShared ? '' : `<button type="button" class="sv-del noprint" data-del="${esc(x.no)}" aria-label="${esc(r.name)}を保存から外す">外す</button>`}</div>
  <dl class="sv-dl">${row('電話', tel)}${row('住所', esc(r.addr))}${row('運営', esc(r.corp))}${row('時間（平日）', esc(r.hours))}${row('定休日', esc(r.closed))}${row('定員', esc(r.cap))}${row('平均工賃', esc(r.wage))}</dl>
  ${isShared ? '' : `<label class="sv-memo noprint"><span>メモ（見学日・聞いたことなど。この端末にだけ残ります）</span><textarea data-memo="${esc(x.no)}" rows="2" maxlength="500">${esc(memo)}</textarea></label>`}
  <p class="sv-memo-print"${memo ? '' : ' hidden'}>メモ：${esc(memo)}</p>
  <div class="sv-write" aria-hidden="true">メモ</div>
</li>`;
      }).join('');
      renderTable(list);
      $('sv-line').href = 'https://line.me/R/msg/text/?' + encodeURIComponent(
        `気になる事業所の一覧です（${list.length}か所）\n` +
        list.map((x, i) => `${i + 1}. ${(recs[x.no] || cache(x.no) || x).name || ''}`).join('\n') + '\n' + shareUrl());
      drawMap(list);
    }

    function renderTable(list) {
      const rs = list.map(x => recs[x.no] || cache(x.no) || { no: x.no, name: x.name || '' });
      const rows = [
        ['サービス', r => esc((r.svc || []).join('・'))],
        ['電話', r => r.tel ? `<span class="tnum">${esc(r.tel)}</span>` : ''],
        ['住所', r => esc(r.addr)],
        ['運営', r => esc(r.corp)],
        ['時間（平日）', r => esc(r.hours)],
        ['定休日', r => esc(r.closed)],
        ['定員', r => esc(r.cap)],
        ['平均工賃', r => esc(r.wage)],
      ];
      if (!isShared) rows.push(['メモ', r => esc(memoOf(r.no))]);
      const used = rows.filter(([, f]) => rs.some(r => f(r)));
      $('sv-table').innerHTML = `<table class="sv-cmp"><thead><tr><th scope="col"></th>${rs.map((r, i) =>
        `<th scope="col"><span class="sv-num">${i + 1}</span><a href="${esc(facUrl(r.no))}">${esc(r.name)}</a></th>`).join('')}</tr></thead><tbody>${
        used.map(([k, f]) => `<tr><th scope="row">${k}</th>${rs.map(r => `<td>${f(r) || '<span class="note">ー</span>'}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }

    function drawMap(list) {
      const el = $('sv-map');
      const pts = list.map((x, i) => [i, recs[x.no] || cache(x.no)]).filter(([, r]) => r && +r.lat && +r.lng);
      if (!window.L || !pts.length) { el.hidden = true; return; }
      el.hidden = false;
      if (!map) {
        map = L.map(el, { scrollWheelZoom: false, tap: false });
        L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png', {
          maxZoom: 18, attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>'
        }).addTo(map);
        layer = L.layerGroup().addTo(map);
      }
      layer.clearLayers();
      const ll = pts.map(([i, r]) => {
        const p = [+r.lat, +r.lng];
        L.marker(p, { icon: L.divIcon({ className: 'sv-pin', html: `<span>${i + 1}</span>`, iconSize: [28, 28], iconAnchor: [14, 14] }), title: r.name })
          .bindPopup(`<b>${i + 1}. ${esc(r.name)}</b><br>${esc(r.addr)}`).addTo(layer);
        return p;
      });
      bounds = ll;
      map.fitBounds(ll, { padding: [24, 24], maxZoom: 15 });
    }

    function setView(v) {
      view = v;
      $('sv-v-list').setAttribute('aria-pressed', v === 'list');
      $('sv-v-table').setAttribute('aria-pressed', v === 'table');
      $('sv-list').hidden = v !== 'list';
      $('sv-table').hidden = v !== 'table';
      main.classList.toggle('as-table', v === 'table');
    }

    const msg = t => { $('sv-msg').textContent = t; setTimeout(() => { $('sv-msg').textContent = ''; }, 4000); };

    $('sv-v-list').addEventListener('click', () => setView('list'));
    $('sv-v-table').addEventListener('click', () => { renderTable(items()); setView('table'); });
    $('sv-print').addEventListener('click', () => {
      if (window.gtag) gtag('event', 'saved_print', { n: items().length });
      setTimeout(() => window.print(), 300);
    });
    $('sv-line').addEventListener('click', () => { if (window.gtag) gtag('event', 'saved_line', { n: items().length }); });
    $('sv-copy').addEventListener('click', () => {
      const u = shareUrl();
      (navigator.clipboard ? navigator.clipboard.writeText(u) : Promise.reject())
        .then(() => msg('リンクをコピーしました。メールなどに貼りつけて送れます。'))
        .catch(() => { window.prompt('このリンクをコピーしてください', u); });
    });
    $('sv-clear').addEventListener('click', () => {
      if (!window.confirm('保存した事業所とメモをすべて消します。よろしいですか？')) return;
      read().forEach(x => { try { localStorage.removeItem('jf-rec-' + x.no); } catch (e) { /* なし */ } });
      write([]); render(); paint();
    });
    $('sv-import').addEventListener('click', () => {
      const list = read();
      const have = new Set(list.map(x => x.no));
      shared.forEach(no => { if (!have.has(no) && list.length < MAX) list.push({ no, name: (recs[no] || {}).name || '', at: Date.now() }); });
      write(list);
      location.href = new URL('saved/', ROOT).href;
    });
    $('sv-list').addEventListener('click', e => {
      const b = e.target.closest('[data-del]');
      if (!b) return;
      write(read().filter(x => x.no !== b.dataset.del));
      render(); paint();
    });
    $('sv-list').addEventListener('input', e => {
      const t = e.target.closest('[data-memo]');
      if (!t) return;
      setMemo(t.dataset.memo, t.value);
      const pr = t.closest('li').querySelector('.sv-memo-print');
      pr.textContent = 'メモ：' + t.value; pr.hidden = !t.value.trim();
    });

    render();
    // 事業所ページから最新の中身を読み直す（保存したあとに変わっていることがあるため）
    Promise.all(items().map(x => load(x.no).then(r => {
      if (r) {
        recs[x.no] = r;
        if (!isShared) { const list = read(); const y = list.find(z => z.no === x.no); if (y && y.name !== r.name) { y.name = r.name; write(list); } }
      }
    }))).then(() => {
      // メモを書いている途中なら、書き直さない
      if (document.activeElement && document.activeElement.matches('textarea[data-memo]')) return;
      render();
      if (view === 'table') renderTable(items());
    });
  }
})();
