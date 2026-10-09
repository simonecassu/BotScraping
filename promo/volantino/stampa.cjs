const { chromium } = require("playwright");
(async () => {
  const [html, pdf, png] = process.argv.slice(2);
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 794, height: 1123 }, deviceScaleFactor: 2 });
  await p.goto("file://" + html, { waitUntil: "networkidle" }); await p.evaluate(() => document.fonts.ready);
  await p.pdf({ path: pdf, format: "A4", printBackground: true, margin: { top: 0, right: 0, bottom: 0, left: 0 } });
  await p.screenshot({ path: png, fullPage: false }); await b.close();
})();
