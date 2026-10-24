// regional_report.py が書き出した HTML を PDF にする。 node scripts/report_pdf.js <フォルダ>
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
(async () => {
  const dir = path.resolve(process.argv[2]);
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined;
  const b = await chromium.launch({ proxy });
  for (const f of fs.readdirSync(dir).filter((f) => f.endsWith('.html'))) {
    const p = await b.newPage();
    await p.goto('file://' + path.join(dir, f), { waitUntil: 'networkidle' });
    await p.evaluate(() => document.fonts.ready);
    await p.pdf({ path: path.join(dir, f.replace(/\.html$/, '.pdf')), format: 'A4', printBackground: true,
      displayHeaderFooter: true, headerTemplate: '<span></span>',
      footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#5b6c60">ジモふく 地域レポート　<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
      margin: { top: '16mm', bottom: '16mm', left: '14mm', right: '14mm' } });
    await p.close();
    console.log('PDF:', f);
  }
  await b.close();
})();
