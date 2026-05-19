// Generates landing/sitemap.xml including all per-ticker sentiment pages.
// Run after generate-ticker-pages.js.  node generate-sitemap.js

const fs = require('fs');
const path = require('path');
const { TICKERS } = require('./generate-ticker-pages');

const today = new Date().toISOString().slice(0, 10);

const fixed = [
  { loc: 'https://sentimentfx.org/',                changefreq: 'weekly', priority: '1.0' },
  { loc: 'https://sentimentfx.org/privacy.html',    changefreq: 'yearly', priority: '0.3' },
  { loc: 'https://sentimentfx.org/terms.html',      changefreq: 'yearly', priority: '0.3' },
  { loc: 'https://app.sentimentfx.org/',            changefreq: 'weekly', priority: '0.9' },
  { loc: 'https://developers.sentimentfx.org/',     changefreq: 'weekly', priority: '0.8' },
  { loc: 'https://status.sentimentfx.org/',         changefreq: 'daily',  priority: '0.4' },
];

const tickerUrls = TICKERS.map(t => ({
  loc: `https://sentimentfx.org/sentiment/${t.slug}`,
  changefreq: 'daily',
  priority: '0.7',
}));

const all = [...fixed, ...tickerUrls];

const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${all.map(u => `  <url>
    <loc>${u.loc}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>${u.changefreq}</changefreq>
    <priority>${u.priority}</priority>
  </url>`).join('\n')}
</urlset>
`;

fs.writeFileSync(path.join(__dirname, 'sitemap.xml'), xml);
console.log(`Wrote sitemap with ${all.length} URLs`);
