// Generates a static SEO landing page per ticker into landing/sentiment/<slug>.html
// Run with:  node generate-ticker-pages.js
// Output is checked into git and served by Vercel (cleanUrls=true) as /sentiment/<slug>

const fs = require('fs');
const path = require('path');

const TICKERS = [
  // === Crypto ===
  { id: 'BTC',  slug: 'btc',  name: 'Bitcoin',          display: 'BTC',     category: 'crypto', blurb: 'the largest cryptocurrency by market cap and the de-facto reserve asset of the crypto market' },
  { id: 'ETH',  slug: 'eth',  name: 'Ethereum',         display: 'ETH',     category: 'crypto', blurb: 'the leading smart-contract platform, settlement layer for DeFi, NFTs and most L2 networks' },
  { id: 'SOL',  slug: 'sol',  name: 'Solana',           display: 'SOL',     category: 'crypto', blurb: 'a high-throughput L1 blockchain known for low fees and consumer-facing apps' },
  { id: 'XRP',  slug: 'xrp',  name: 'XRP',              display: 'XRP',     category: 'crypto', blurb: 'a cross-border payments token closely tied to Ripple Labs and ongoing US regulatory headlines' },
  { id: 'DOGE', slug: 'doge', name: 'Dogecoin',         display: 'DOGE',    category: 'crypto', blurb: 'a meme-origin cryptocurrency whose price is unusually news- and social-driven' },

  // === FX ===
  { id: 'EURUSD', slug: 'eurusd', name: 'Euro / US Dollar',                display: 'EUR/USD', category: 'fx', blurb: 'the most heavily traded FX pair, driven by ECB and Federal Reserve policy' },
  { id: 'GBPUSD', slug: 'gbpusd', name: 'British Pound / US Dollar',       display: 'GBP/USD', category: 'fx', blurb: 'the cable, sensitive to Bank of England decisions and UK macro headlines' },
  { id: 'USDJPY', slug: 'usdjpy', name: 'US Dollar / Japanese Yen',        display: 'USD/JPY', category: 'fx', blurb: 'driven by Fed–BoJ policy divergence and global risk sentiment' },
  { id: 'AUDUSD', slug: 'audusd', name: 'Australian Dollar / US Dollar',   display: 'AUD/USD', category: 'fx', blurb: 'a commodity-linked currency pair sensitive to China demand and RBA policy' },
  { id: 'USDCAD', slug: 'usdcad', name: 'US Dollar / Canadian Dollar',     display: 'USD/CAD', category: 'fx', blurb: 'closely tied to crude oil prices and Bank of Canada policy' },
  { id: 'USDCHF', slug: 'usdchf', name: 'US Dollar / Swiss Franc',         display: 'USD/CHF', category: 'fx', blurb: 'a classic safe-haven pair that reacts strongly to risk-off headlines' },
  { id: 'NZDUSD', slug: 'nzdusd', name: 'New Zealand Dollar / US Dollar',  display: 'NZD/USD', category: 'fx', blurb: 'the kiwi — a commodity- and risk-sensitive pair driven by RBNZ policy' },

  // === Stocks ===
  { id: 'AAPL',  slug: 'aapl',  name: 'Apple Inc.',         display: 'AAPL',  category: 'stocks', blurb: 'the largest US consumer-tech company; iPhone cycle and services revenue dominate headlines' },
  { id: 'MSFT',  slug: 'msft',  name: 'Microsoft',          display: 'MSFT',  category: 'stocks', blurb: 'a cloud and enterprise software giant; AI partnerships and Azure growth drive sentiment' },
  { id: 'GOOGL', slug: 'googl', name: 'Alphabet (Google)',  display: 'GOOGL', category: 'stocks', blurb: 'parent company of Google Search, YouTube and Cloud; antitrust and AI headlines move the stock' },
  { id: 'AMZN',  slug: 'amzn',  name: 'Amazon',             display: 'AMZN',  category: 'stocks', blurb: 'e-commerce and AWS cloud leader; logistics, ads and AI infrastructure dominate news flow' },
  { id: 'META',  slug: 'meta',  name: 'Meta Platforms',     display: 'META',  category: 'stocks', blurb: 'owner of Facebook, Instagram and WhatsApp; ad revenue and AI capex shape sentiment' },
  { id: 'NVDA',  slug: 'nvda',  name: 'NVIDIA',             display: 'NVDA',  category: 'stocks', blurb: 'the dominant AI accelerator vendor; datacenter GPU demand drives most headlines' },
  { id: 'TSLA',  slug: 'tsla',  name: 'Tesla',              display: 'TSLA',  category: 'stocks', blurb: 'EV and energy company whose price is famously news- and Musk-driven' },
  { id: 'JPM',   slug: 'jpm',   name: 'JPMorgan Chase',     display: 'JPM',   category: 'stocks', blurb: 'the largest US bank; sentiment tracks rates, credit conditions and earnings' },
  { id: 'BAC',   slug: 'bac',   name: 'Bank of America',    display: 'BAC',   category: 'stocks', blurb: 'major US bank sensitive to interest rates and consumer credit headlines' },
  { id: 'GS',    slug: 'gs',    name: 'Goldman Sachs',      display: 'GS',    category: 'stocks', blurb: 'investment bank highly correlated with capital-markets activity and dealmaking news' },
  { id: 'V',     slug: 'v',     name: 'Visa',               display: 'V',     category: 'stocks', blurb: 'global payments network; consumer spending and regulatory headlines move sentiment' },
  { id: 'MA',    slug: 'ma',    name: 'Mastercard',         display: 'MA',    category: 'stocks', blurb: 'global payments network similar to Visa in headline drivers' },
  { id: 'XOM',   slug: 'xom',   name: 'ExxonMobil',         display: 'XOM',   category: 'stocks', blurb: 'the largest US integrated oil major; crude prices and energy policy drive headlines' },
  { id: 'JNJ',   slug: 'jnj',   name: 'Johnson & Johnson',  display: 'JNJ',   category: 'stocks', blurb: 'a healthcare and pharma giant; litigation, drug approvals and dividends drive news' },
  { id: 'AMD',   slug: 'amd',   name: 'AMD',                display: 'AMD',   category: 'stocks', blurb: 'CPU/GPU rival to Intel and Nvidia; AI accelerator and datacenter wins move sentiment' },
  { id: 'NFLX',  slug: 'nflx',  name: 'Netflix',            display: 'NFLX',  category: 'stocks', blurb: 'global streaming leader; subscriber growth and content slate dominate news flow' },
  { id: 'WMT',   slug: 'wmt',   name: 'Walmart',            display: 'WMT',   category: 'stocks', blurb: 'the largest US retailer; consumer-spending health and e-commerce growth drive headlines' },
  { id: 'UBER',  slug: 'uber',  name: 'Uber',               display: 'UBER',  category: 'stocks', blurb: 'rideshare and delivery platform; regulation and unit economics move sentiment' },
  { id: 'CRM',   slug: 'crm',   name: 'Salesforce',         display: 'CRM',   category: 'stocks', blurb: 'enterprise SaaS leader; AI products and seat growth drive sentiment' },
  { id: 'PLTR',  slug: 'pltr',  name: 'Palantir',           display: 'PLTR',  category: 'stocks', blurb: 'data analytics company with government and commercial AI deals shaping headlines' },

  // === ETFs ===
  { id: 'SPY',  slug: 'spy',  name: 'SPDR S&P 500 ETF',       display: 'SPY',  category: 'etfs', blurb: 'the benchmark US large-cap ETF — sentiment proxies the broad US equity market' },
  { id: 'QQQ',  slug: 'qqq',  name: 'Invesco Nasdaq 100 ETF', display: 'QQQ',  category: 'etfs', blurb: 'tracks the Nasdaq 100; sentiment is dominated by mega-cap tech news flow' },
  { id: 'GLD',  slug: 'gld',  name: 'SPDR Gold Shares',       display: 'GLD',  category: 'etfs', blurb: 'physical-gold-backed ETF; macro risk-off and inflation headlines drive sentiment' },
  { id: 'SLV',  slug: 'slv',  name: 'iShares Silver Trust',   display: 'SLV',  category: 'etfs', blurb: 'physical-silver ETF, sensitive to industrial demand and precious-metals macro' },
  { id: 'USO',  slug: 'uso',  name: 'United States Oil Fund', display: 'USO',  category: 'etfs', blurb: 'tracks WTI crude futures; OPEC and supply/demand headlines dominate sentiment' },
  { id: 'ARKK', slug: 'arkk', name: 'ARK Innovation ETF',     display: 'ARKK', category: 'etfs', blurb: 'high-growth, high-beta innovation ETF — sentiment closely tracks rate expectations' },

  // === Commodities (futures) ===
  { id: 'GC=F', slug: 'gold-futures',          name: 'Gold Futures (GC=F)',        display: 'GC=F', category: 'commodities', blurb: 'COMEX gold futures — the headline benchmark for the gold market' },
  { id: 'SI=F', slug: 'silver-futures',        name: 'Silver Futures (SI=F)',      display: 'SI=F', category: 'commodities', blurb: 'COMEX silver futures — sensitive to industrial demand and precious-metals macro' },
  { id: 'CL=F', slug: 'crude-oil-futures',     name: 'WTI Crude Oil Futures (CL=F)', display: 'CL=F', category: 'commodities', blurb: 'NYMEX WTI crude oil futures — the global oil price benchmark' },
  { id: 'NG=F', slug: 'natural-gas-futures',   name: 'Natural Gas Futures (NG=F)', display: 'NG=F', category: 'commodities', blurb: 'NYMEX natural gas futures — sensitive to weather, storage and LNG headlines' },
];

const CATEGORY_LABEL = {
  crypto: 'Crypto',
  fx: 'Foreign Exchange',
  stocks: 'Stocks',
  etfs: 'ETFs',
  commodities: 'Commodities',
};

function relatedTickers(ticker) {
  return TICKERS.filter(t => t.category === ticker.category && t.id !== ticker.id).slice(0, 7);
}

function pageHtml(t) {
  const related = relatedTickers(t);
  const url = `https://sentimentfx.org/sentiment/${t.slug}`;
  const title = `${t.name} (${t.display}) Sentiment Analysis — News Sentiment vs Price | SentimentFX`;
  const desc = `Live ${t.display} news sentiment analysis from SentimentFX. FinBERT-scored headlines, sentiment vs price chart, and correlation analytics for ${t.name} — updated hourly.`;

  const faqs = [
    {
      q: `How is ${t.display} sentiment calculated?`,
      a: `SentimentFX scrapes ${t.name} news headlines from major financial outlets every hour and scores each one with FinBERT, a finance-tuned transformer model. The score is the model's positive probability minus negative probability, giving a value between -1 (very bearish) and +1 (very bullish).`,
    },
    {
      q: `How often is ${t.display} sentiment data updated?`,
      a: `The scraper runs every hour, pulling new ${t.display} headlines from GNews and ${t.category === 'crypto' ? 'crypto-specific RSS feeds (CoinTelegraph, CoinDesk, Decrypt and others)' : t.category === 'fx' ? 'forex RSS feeds (FXStreet, ForexLive, DailyFX)' : 'Yahoo Finance RSS feeds'}. New sentiment scores are usually live within 1–2 minutes of scrape completion.`,
    },
    {
      q: `Can I correlate ${t.display} sentiment with its price?`,
      a: `Yes — the SentimentFX dashboard plots ${t.display} sentiment against price on the same chart, and the correlation endpoint returns a Pearson r value, 95% confidence interval and strength label over a rolling 180-day window. You can also pull this via the public REST API at developers.sentimentfx.org.`,
    },
    {
      q: `Is ${t.display} sentiment data available via API?`,
      a: `Yes. The SentimentFX public API exposes /v1/sentiment/${t.id}, /v1/summary/${t.id}, /v1/prices/${t.id} and /v1/correlation/${t.id} endpoints. Authentication is via X-API-Key header. See the developer portal for full reference.`,
    },
  ];

  const faqJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faqs.map(f => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: { '@type': 'Answer', text: f.a },
    })),
  };

  const breadcrumbJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'SentimentFX', item: 'https://sentimentfx.org/' },
      { '@type': 'ListItem', position: 2, name: `${CATEGORY_LABEL[t.category]} Sentiment`, item: `https://sentimentfx.org/#coverage` },
      { '@type': 'ListItem', position: 3, name: `${t.name} (${t.display})`, item: url },
    ],
  };

  const datasetJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Dataset',
    name: `${t.name} (${t.display}) News Sentiment Dataset`,
    description: `Hourly FinBERT-scored news sentiment headlines for ${t.name} (${t.display}), with matched daily price data in ${t.category === 'crypto' ? 'GBP' : t.category === 'fx' ? 'raw exchange rate' : 'USD'}. Maintained by SentimentFX.`,
    url,
    keywords: [`${t.display} sentiment`, `${t.name} sentiment analysis`, 'FinBERT', 'financial NLP', 'news sentiment'],
    creator: { '@type': 'Organization', name: 'SentimentFX', url: 'https://sentimentfx.org/' },
    license: 'https://sentimentfx.org/terms.html',
  };

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${title}</title>
  <meta name="description" content="${desc}">
  <meta name="keywords" content="${t.display} sentiment, ${t.name} sentiment analysis, ${t.display} news sentiment, ${t.display} FinBERT, ${t.display} market sentiment, ${t.display} sentiment API">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <meta name="theme-color" content="#0b0e14">
  <link rel="canonical" href="${url}">

  <meta property="og:site_name" content="SentimentFX">
  <meta property="og:title" content="${title}">
  <meta property="og:description" content="${desc}">
  <meta property="og:url" content="${url}">
  <meta property="og:type" content="article">
  <meta property="og:image" content="https://sentimentfx.org/dashboard.jpeg">
  <meta property="og:locale" content="en_GB">

  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="${title}">
  <meta name="twitter:description" content="${desc}">
  <meta name="twitter:image" content="https://sentimentfx.org/dashboard.jpeg">

  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">

  <script type="application/ld+json">${JSON.stringify(breadcrumbJsonLd)}</script>
  <script type="application/ld+json">${JSON.stringify(faqJsonLd)}</script>
  <script type="application/ld+json">${JSON.stringify(datasetJsonLd)}</script>

  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
    :root{
      --bg:#0b0e14;--surface:#11151d;--surface2:#171c26;
      --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.14);
      --text:#e8ecf3;--muted:#8b95a7;
      --accent:#f0b429;--accent2:#6cb2ff;
      --positive:#42c768;--negative:#f26d64;
      --mono:'IBM Plex Mono',monospace;--sans:'Geist',sans-serif;
    }
    body{background:var(--bg);color:var(--text);font-family:var(--sans);line-height:1.65;}
    a{color:var(--accent);text-decoration:none;}
    a:hover{text-decoration:underline;}
    nav{display:flex;align-items:center;justify-content:space-between;padding:14px 40px;border-bottom:1px solid var(--border);background:var(--surface);position:sticky;top:0;z-index:100;}
    .nav-logo{font-family:var(--mono);font-size:13px;font-weight:600;letter-spacing:0.15em;color:var(--accent);text-transform:uppercase;}
    .nav-logo span{color:var(--muted);font-weight:400;}
    .nav-links{display:flex;gap:24px;list-style:none;}
    .nav-links a{font-family:var(--mono);font-size:11px;letter-spacing:0.08em;color:var(--muted);}
    .nav-links a:hover{color:var(--text);text-decoration:none;}
    .nav-cta{background:var(--accent);color:#0b0e14!important;padding:6px 14px;border-radius:8px;font-weight:600;}

    main{max-width:880px;margin:0 auto;padding:48px 24px;}
    .breadcrumb{font-family:var(--mono);font-size:10px;letter-spacing:0.1em;color:var(--muted);margin-bottom:24px;text-transform:uppercase;}
    .breadcrumb a{color:var(--muted);}
    .breadcrumb a:hover{color:var(--text);text-decoration:none;}
    .breadcrumb span{margin:0 8px;color:var(--border2);}

    .ticker-label{display:inline-block;font-family:var(--mono);font-size:10px;letter-spacing:0.2em;color:var(--accent);text-transform:uppercase;margin-bottom:12px;}
    h1{font-family:var(--mono);font-size:30px;font-weight:600;line-height:1.2;margin-bottom:14px;}
    h1 .sub{color:var(--muted);font-weight:400;}
    .lede{font-size:17px;color:var(--text);max-width:680px;margin-bottom:28px;line-height:1.6;}

    .live-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:24px;margin-bottom:32px;display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:18px;}
    .live-cell .live-label{font-family:var(--mono);font-size:9px;letter-spacing:0.15em;color:var(--muted);text-transform:uppercase;margin-bottom:6px;}
    .live-cell .live-value{font-family:var(--mono);font-size:22px;font-weight:600;color:var(--text);}
    .live-cell.pos .live-value{color:var(--positive);}
    .live-cell.neg .live-value{color:var(--negative);}

    .cta{display:inline-block;background:var(--accent);color:#0b0e14!important;padding:12px 24px;border-radius:8px;font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;margin-right:10px;}
    .cta-secondary{background:transparent;color:var(--text)!important;border:1px solid var(--border2);}
    .cta:hover{text-decoration:none;}

    h2{font-family:var(--mono);font-size:18px;font-weight:600;margin-top:48px;margin-bottom:14px;}
    p{margin-bottom:14px;color:#c9d1d9;}

    .related{margin-top:48px;padding-top:32px;border-top:1px solid var(--border);}
    .related-title{font-family:var(--mono);font-size:10px;letter-spacing:0.15em;color:var(--muted);text-transform:uppercase;margin-bottom:14px;}
    .related-chips{display:flex;flex-wrap:wrap;gap:8px;}
    .chip{display:inline-block;font-family:var(--mono);font-size:11px;letter-spacing:0.06em;background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 12px;border-radius:999px;}
    .chip:hover{border-color:var(--accent);text-decoration:none;}

    .faq{margin-top:48px;}
    .faq-item{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px 22px;margin-bottom:10px;}
    .faq-q{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;}
    .faq-a{font-size:14px;color:#c9d1d9;line-height:1.6;}

    footer{border-top:1px solid var(--border);padding:24px 40px;margin-top:64px;font-family:var(--mono);font-size:10px;letter-spacing:0.06em;color:var(--muted);display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;}
    footer a{color:var(--muted);}
    footer a:hover{color:var(--accent);text-decoration:none;}

    @media(max-width:600px){
      nav{padding:12px 16px;}
      .nav-links{display:none;}
      main{padding:32px 16px;}
      h1{font-size:24px;}
      footer{padding:16px;flex-direction:column;}
    }
  </style>
</head>
<body>

<nav>
  <a href="/" class="nav-logo">SentimentFX <span>/ ${t.display}</span></a>
  <ul class="nav-links">
    <li><a href="/">Home</a></li>
    <li><a href="/#coverage">Coverage</a></li>
    <li><a href="/#pricing">Pricing</a></li>
    <li><a href="https://developers.sentimentfx.org">Developers</a></li>
    <li><a href="https://app.sentimentfx.org?auth=signup" class="nav-cta">Create Account</a></li>
  </ul>
</nav>

<main>
  <div class="breadcrumb">
    <a href="/">SentimentFX</a><span>/</span><a href="/#coverage">${CATEGORY_LABEL[t.category]}</a><span>/</span>${t.display}
  </div>

  <div class="ticker-label">${CATEGORY_LABEL[t.category]} · ${t.display}</div>
  <h1>${t.name} <span class="sub">(${t.display})</span> Sentiment Analysis</h1>

  <p class="lede">
    Live news sentiment for ${t.name} — ${t.blurb}. SentimentFX scores every ${t.display}
    headline with FinBERT and tracks how sentiment moves alongside price, updated hourly.
  </p>

  <div class="live-card" id="live-card">
    <div class="live-cell">
      <div class="live-label">Avg sentiment (7d)</div>
      <div class="live-value" id="live-sentiment">—</div>
    </div>
    <div class="live-cell">
      <div class="live-label">Headlines (30d)</div>
      <div class="live-value" id="live-headlines">—</div>
    </div>
    <div class="live-cell">
      <div class="live-label">Latest price</div>
      <div class="live-value" id="live-price">—</div>
    </div>
    <div class="live-cell">
      <div class="live-label">Sentiment trend</div>
      <div class="live-value" id="live-trend">—</div>
    </div>
  </div>

  <div>
    <a href="https://app.sentimentfx.org?ticker=${t.id}" class="cta">View live chart</a>
    <a href="https://developers.sentimentfx.org#sentiment" class="cta cta-secondary">Get via API</a>
  </div>

  <h2>What we track for ${t.display}</h2>
  <p>
    Every hour, SentimentFX collects the latest ${t.name} news from major financial publishers
    and RSS feeds, then scores each headline using FinBERT — a transformer model fine-tuned
    on financial text. The output is a sentiment score between -1 (very bearish) and +1
    (very bullish), and we plot the daily average against ${t.display} price so you can
    see how news flow moves with the market.
  </p>
  <p>
    ${t.name} is ${t.blurb}, which makes ${t.display} an especially useful ticker for
    sentiment-driven analysis. Our pipeline ${t.category === 'crypto' ? 'pulls from CoinTelegraph, CoinDesk, Decrypt, Blockworks, BeInCrypto and Reddit alongside GNews search results' : t.category === 'fx' ? 'pulls from FXStreet, ForexLive, DailyFX, ActionForex and Reddit alongside GNews search results' : 'pulls from Yahoo Finance and other major financial newswires'}, deduplicates by URL, and only stores headlines whose title actually mentions ${t.display} or a recognised alias.
  </p>

  <h2>Sentiment vs price correlation</h2>
  <p>
    The dashboard plots ${t.display} sentiment against price on a single chart, with a
    rolling 180-day correlation analysis that reports Pearson r, p-value, a 95% confidence
    interval and a plain-English strength label (strong / weak / inconclusive). You can also
    pull the raw correlation via the public API endpoint <a href="https://developers.sentimentfx.org">/v1/correlation/${t.id}</a>.
  </p>

  <h2>Developer access</h2>
  <p>
    All ${t.display} sentiment, price and correlation data is available via the SentimentFX
    REST API. Generate a key on the
    <a href="https://developers.sentimentfx.org">developer portal</a>, then call
    <code style="font-family:var(--mono);background:var(--surface);padding:2px 6px;border-radius:6px;font-size:13px;">GET /v1/sentiment/${t.id}</code>
    with an <code style="font-family:var(--mono);background:var(--surface);padding:2px 6px;border-radius:6px;font-size:13px;">X-API-Key</code>
    header. Free tier included, metered billing beyond that.
  </p>

  <div class="related">
    <div class="related-title">Related ${CATEGORY_LABEL[t.category]} tickers</div>
    <div class="related-chips">
      ${related.map(r => `<a class="chip" href="/sentiment/${r.slug}">${r.display}</a>`).join('\n      ')}
    </div>
  </div>

  <div class="faq">
    <h2 style="margin-top:0;">${t.display} sentiment FAQ</h2>
    ${faqs.map(f => `
    <div class="faq-item">
      <div class="faq-q">${f.q}</div>
      <div class="faq-a">${f.a}</div>
    </div>`).join('')}
  </div>
</main>

<footer>
  <span>SentimentFX — News sentiment intelligence for 42 tickers across crypto, FX, stocks, ETFs and commodities.</span>
  <span>
    <a href="/">Home</a> ·
    <a href="https://app.sentimentfx.org">Dashboard</a> ·
    <a href="https://developers.sentimentfx.org">API</a> ·
    <a href="https://status.sentimentfx.org">Status</a> ·
    <a href="/privacy">Privacy</a> ·
    <a href="/terms">Terms</a>
  </span>
</footer>

<script>
  const API = 'https://api.sentimentfx.org';
  const TICKER = ${JSON.stringify(t.id)};
  const CATEGORY = ${JSON.stringify(t.category)};

  function fmtPrice(n) {
    if (n == null || isNaN(n)) return '—';
    if (n > 1000) return n.toLocaleString(undefined, {maximumFractionDigits: 0});
    if (n > 1) return n.toFixed(2);
    return n.toFixed(4);
  }

  function fmtSent(n) {
    if (n == null || isNaN(n)) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(3);
  }

  fetch(API + '/dashboard/' + encodeURIComponent(TICKER) + '?days=30&limit=200')
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (!data || typeof data !== 'object') return;
      const headlines = Array.isArray(data.sentiment) ? data.sentiment : [];
      const prices = Array.isArray(data.prices) ? data.prices : [];
      const totalIn30d = data.pagination && data.pagination.total != null ? data.pagination.total : headlines.length;

      // Latest sentiment — average score over the most recent 7 days
      const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
      const recent = headlines.filter(h => {
        const t = new Date(h.date.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(h.date) ? h.date : h.date + 'Z').getTime();
        return t >= sevenDaysAgo && typeof h.score === 'number';
      });
      if (recent.length) {
        const avg = recent.reduce((s,h) => s + h.score, 0) / recent.length;
        const el = document.getElementById('live-sentiment');
        el.textContent = fmtSent(avg);
        el.parentElement.classList.add(avg >= 0 ? 'pos' : 'neg');
      }

      // Headline count over the 30-day window — use server-provided total
      document.getElementById('live-headlines').textContent = totalIn30d.toLocaleString();

      // Latest price — /dashboard returns prices ordered desc, so first is latest
      if (prices.length && prices[0].close_price != null) {
        const isGbp = CATEGORY === 'crypto';   // crypto tickers are -GBP on yfinance
        const isUsd = CATEGORY === 'stocks' || CATEGORY === 'etfs' || CATEGORY === 'commodities';
        const prefix = isGbp ? '£' : isUsd ? '$' : '';
        document.getElementById('live-price').textContent = prefix + fmtPrice(prices[0].close_price);
      }

      // Trend — compare first half vs second half of the last 7 days
      if (recent.length >= 4) {
        const sorted = [...recent].sort((a,b) => new Date(a.date) - new Date(b.date));
        const half = Math.floor(sorted.length / 2);
        const early = sorted.slice(0, half).reduce((s,h) => s + h.score, 0) / half;
        const late = sorted.slice(half).reduce((s,h) => s + h.score, 0) / (sorted.length - half);
        const delta = late - early;
        const trendEl = document.getElementById('live-trend');
        trendEl.textContent = delta > 0.02 ? 'Rising' : delta < -0.02 ? 'Falling' : 'Flat';
        if (delta > 0.02) trendEl.parentElement.classList.add('pos');
        else if (delta < -0.02) trendEl.parentElement.classList.add('neg');
      }
    })
    .catch(() => {});
</script>

</body>
</html>
`;
}

function main() {
  const outDir = path.join(__dirname, 'sentiment');
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  TICKERS.forEach(t => {
    const html = pageHtml(t);
    const file = path.join(outDir, `${t.slug}.html`);
    fs.writeFileSync(file, html);
  });

  console.log(`Generated ${TICKERS.length} ticker pages in ${outDir}`);

  // Also emit a JSON manifest so sitemap generation can read it
  const manifestPath = path.join(__dirname, 'sentiment-tickers.json');
  fs.writeFileSync(manifestPath, JSON.stringify(TICKERS.map(t => ({ slug: t.slug, id: t.id })), null, 2));
}

if (require.main === module) main();
module.exports = { TICKERS };
