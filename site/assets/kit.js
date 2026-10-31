// 事業所の方へ：事業所ページの QR コード・バナー・案内カード（/kit/?f=事業所番号）
(function () {
  const $ = id => document.getElementById(id);
  const no = new URLSearchParams(location.search).get('f') || '';
  if (!/^[0-9A-Za-z]{1,20}$/.test(no)) { $('kit-none').hidden = false; return; }
  const me = document.currentScript || document.querySelector('script[src*="assets/kit.js"]');
  const ROOT = me ? new URL('../', me.src) : new URL('/', location.href);
  // 公開先の住所（canonical からとる。ローカルで見ているときも本番の住所にする）
  const canon = document.querySelector('link[rel=canonical]');
  const SITE = canon ? canon.href.replace(/kit\/?$/, '') : ROOT.href;
  const url = SITE + 'f/' + no + '/';
  const msg = t => { $('kit-msg').textContent = t; setTimeout(() => { $('kit-msg').textContent = ''; }, 4000); };
  const copy = (t, done) => (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject())
    .then(() => msg(done)).catch(() => window.prompt('コピーしてください', t));

  function qr(size) {
    const q = qrcode(0, 'M');
    q.addData(url);
    q.make();
    const n = q.getModuleCount(), cell = Math.floor(size / (n + 8)), pad = Math.floor((size - cell * n) / 2);
    const c = document.createElement('canvas');
    c.width = c.height = size;
    const g = c.getContext('2d');
    g.fillStyle = '#fff'; g.fillRect(0, 0, size, size);
    g.fillStyle = '#000';
    for (let r = 0; r < n; r++) for (let k = 0; k < n; k++) if (q.isDark(r, k)) g.fillRect(pad + k * cell, pad + r * cell, cell, cell);
    return c;
  }

  fetch(new URL(`f/${no}/`, ROOT)).then(r => r.ok ? r.text() : '').then(t => {
    const m = t.match(/<script type="application\/json" id="fav-data">([\s\S]*?)<\/script>/);
    if (!m) { $('kit-none').hidden = false; return; }
    const rec = JSON.parse(m[1]);
    $('kit').hidden = false;
    $('kit-name').textContent = rec.name;
    $('kit-card-name').textContent = rec.name;
    $('kit-url').textContent = url; $('kit-url').href = url;
    $('kit-fix').href = new URL(`contact/?f=${no}&fn=${encodeURIComponent(rec.name)}`, ROOT).href;
    const big = qr(600);
    const img = new Image(); img.src = big.toDataURL('image/png'); img.alt = ''; img.width = 220; img.height = 220;
    $('kit-qr').appendChild(img);
    const img2 = img.cloneNode(); img2.width = img2.height = 150;
    $('kit-card-qr').appendChild(img2);
    $('kit-code').value = `<a href="${url}" target="_blank" rel="noopener"><img src="${SITE}badge.svg" width="200" height="56" alt="ジモふくに掲載されています"></a>`;
    $('kit-png').addEventListener('click', () => {
      const a = document.createElement('a');
      a.href = qr(1200).toDataURL('image/png'); a.download = `jimofuku_qr_${no}.png`;
      document.body.appendChild(a); a.click(); a.remove();
      if (window.gtag) gtag('event', 'kit_qr_png');
    });
    $('kit-print').addEventListener('click', () => { if (window.gtag) gtag('event', 'kit_card_print'); window.print(); });
    $('kit-copy-url').addEventListener('click', () => copy(url, 'コピーしました。'));
    $('kit-copy-code').addEventListener('click', () => { copy($('kit-code').value, 'コードをコピーしました。'); if (window.gtag) gtag('event', 'kit_badge_copy'); });
  }).catch(() => { $('kit-none').hidden = false; });
})();
