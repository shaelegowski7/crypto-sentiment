// Shared constants used by the dashboard and the standalone pages.
export const FX_LABELS = { EURUSD: "EUR/USD", GBPUSD: "GBP/USD", USDJPY: "USD/JPY", AUDUSD: "AUD/USD", USDCAD: "USD/CAD", USDCHF: "USD/CHF", NZDUSD: "NZD/USD" }
export const COMMODITY_LABELS = { "GC=F": "Gold", "SI=F": "Silver", "CL=F": "Oil", "NG=F": "Nat Gas" }

export const TICKER_SLUGS = {
  BTC: "btc", ETH: "eth", SOL: "sol", XRP: "xrp", DOGE: "doge",
  EURUSD: "eurusd", GBPUSD: "gbpusd", USDJPY: "usdjpy", AUDUSD: "audusd",
  USDCAD: "usdcad", USDCHF: "usdchf", NZDUSD: "nzdusd",
  AAPL: "aapl", MSFT: "msft", GOOGL: "googl", AMZN: "amzn", META: "meta",
  NVDA: "nvda", TSLA: "tsla", JPM: "jpm", BAC: "bac", GS: "gs", V: "v",
  MA: "ma", XOM: "xom", JNJ: "jnj", AMD: "amd", NFLX: "nflx", WMT: "wmt",
  UBER: "uber", CRM: "crm", PLTR: "pltr",
  SPY: "spy", QQQ: "qqq", GLD: "gld", SLV: "slv", USO: "uso", ARKK: "arkk",
  "GC=F": "gold-futures", "SI=F": "silver-futures",
  "CL=F": "crude-oil-futures", "NG=F": "natural-gas-futures",
}

// Plain-English name + one-line description + a reference link for every
// tracked ticker, so someone who doesn't recognise "GC=F" or "PLTR" isn't
// left guessing. Links point at Wikipedia — the one reference source stable
// and notable enough to have a correct, verifiable page for all 42 tickers,
// including FX pairs and commodity futures where no single company/site
// exists to link to. Every URL below was checked (2026-07-04): several
// ticker-symbol pages (GS, XOM, BAC, CRM) turned out to be disambiguation
// pages, not the company, so don't assume `/wiki/<TICKER>` works — verify
// before adding new entries.
export const TICKER_INFO = {
  BTC:  { name: "Bitcoin", blurb: "The original cryptocurrency — decentralized digital money with no central bank.", url: "https://en.wikipedia.org/wiki/Bitcoin" },
  ETH:  { name: "Ethereum", blurb: "A blockchain for smart contracts and apps; ETH is its native currency.", url: "https://en.wikipedia.org/wiki/Ethereum" },
  SOL:  { name: "Solana", blurb: "A fast, low-fee blockchain for apps and smart contracts.", url: "https://en.wikipedia.org/wiki/Solana_(blockchain_platform)" },
  XRP:  { name: "XRP", blurb: "A cryptocurrency built for fast, low-cost cross-border payments.", url: "https://en.wikipedia.org/wiki/XRP" },
  DOGE: { name: "Dogecoin", blurb: "A cryptocurrency that started as a joke, now widely traded.", url: "https://en.wikipedia.org/wiki/Dogecoin" },

  EURUSD: { name: "Euro / US Dollar", blurb: "Exchange rate between the Euro and the US Dollar — the world's most-traded currency pair.", url: "https://en.wikipedia.org/wiki/Euro" },
  GBPUSD: { name: "British Pound / US Dollar", blurb: "Exchange rate between the British Pound and the US Dollar.", url: "https://en.wikipedia.org/wiki/Pound_sterling" },
  USDJPY: { name: "US Dollar / Japanese Yen", blurb: "Exchange rate between the US Dollar and the Japanese Yen.", url: "https://en.wikipedia.org/wiki/Japanese_yen" },
  AUDUSD: { name: "Australian Dollar / US Dollar", blurb: "Exchange rate between the Australian Dollar and the US Dollar.", url: "https://en.wikipedia.org/wiki/Australian_dollar" },
  USDCAD: { name: "US Dollar / Canadian Dollar", blurb: "Exchange rate between the US Dollar and the Canadian Dollar.", url: "https://en.wikipedia.org/wiki/Canadian_dollar" },
  USDCHF: { name: "US Dollar / Swiss Franc", blurb: "Exchange rate between the US Dollar and the Swiss Franc.", url: "https://en.wikipedia.org/wiki/Swiss_franc" },
  NZDUSD: { name: "New Zealand Dollar / US Dollar", blurb: "Exchange rate between the New Zealand Dollar and the US Dollar.", url: "https://en.wikipedia.org/wiki/New_Zealand_dollar" },

  AAPL:  { name: "Apple Inc.", blurb: "Maker of the iPhone, Mac and other consumer electronics.", url: "https://en.wikipedia.org/wiki/Apple_Inc." },
  MSFT:  { name: "Microsoft", blurb: "Software and cloud giant behind Windows, Office and Azure.", url: "https://en.wikipedia.org/wiki/Microsoft" },
  GOOGL: { name: "Alphabet Inc.", blurb: "Parent company of Google, YouTube and other tech businesses.", url: "https://en.wikipedia.org/wiki/Alphabet_Inc." },
  AMZN:  { name: "Amazon", blurb: "E-commerce giant and owner of the AWS cloud platform.", url: "https://en.wikipedia.org/wiki/Amazon_(company)" },
  META:  { name: "Meta Platforms", blurb: "Parent company of Facebook, Instagram and WhatsApp.", url: "https://en.wikipedia.org/wiki/Meta_Platforms" },
  NVDA:  { name: "Nvidia", blurb: "Designs the GPUs powering gaming and AI.", url: "https://en.wikipedia.org/wiki/Nvidia" },
  TSLA:  { name: "Tesla, Inc.", blurb: "Electric vehicle and clean energy company.", url: "https://en.wikipedia.org/wiki/Tesla,_Inc." },
  JPM:   { name: "JPMorgan Chase", blurb: "The largest bank in the United States.", url: "https://en.wikipedia.org/wiki/JPMorgan_Chase" },
  BAC:   { name: "Bank of America", blurb: "One of the largest banks in the United States.", url: "https://en.wikipedia.org/wiki/Bank_of_America" },
  GS:    { name: "Goldman Sachs", blurb: "A major global investment bank.", url: "https://en.wikipedia.org/wiki/Goldman_Sachs" },
  V:     { name: "Visa Inc.", blurb: "Operates the world's largest card payment network.", url: "https://en.wikipedia.org/wiki/Visa_Inc." },
  MA:    { name: "Mastercard", blurb: "Global payment card and financial services company.", url: "https://en.wikipedia.org/wiki/Mastercard" },
  XOM:   { name: "ExxonMobil", blurb: "One of the world's largest oil and gas companies.", url: "https://en.wikipedia.org/wiki/ExxonMobil" },
  JNJ:   { name: "Johnson & Johnson", blurb: "Pharmaceutical and consumer healthcare company.", url: "https://en.wikipedia.org/wiki/Johnson_%26_Johnson" },
  AMD:   { name: "AMD", blurb: "Designs CPUs and GPUs, a rival to Intel and Nvidia.", url: "https://en.wikipedia.org/wiki/AMD" },
  NFLX:  { name: "Netflix", blurb: "Subscription video streaming service.", url: "https://en.wikipedia.org/wiki/Netflix" },
  WMT:   { name: "Walmart", blurb: "The world's largest retailer by revenue.", url: "https://en.wikipedia.org/wiki/Walmart" },
  UBER:  { name: "Uber", blurb: "Ride-hailing, delivery and freight company.", url: "https://en.wikipedia.org/wiki/Uber" },
  CRM:   { name: "Salesforce", blurb: "Cloud-based customer relationship management software.", url: "https://en.wikipedia.org/wiki/Salesforce" },
  PLTR:  { name: "Palantir Technologies", blurb: "Data analytics software, notably for government and defense.", url: "https://en.wikipedia.org/wiki/PLTR" },

  SPY:  { name: "SPDR S&P 500 ETF Trust", blurb: "The oldest and largest ETF, tracking the S&P 500 index of major US companies.", url: "https://en.wikipedia.org/wiki/SPDR_S%26P_500_Trust_ETF" },
  QQQ:  { name: "Invesco QQQ", blurb: "Tracks the Nasdaq-100, dominated by large tech companies.", url: "https://en.wikipedia.org/wiki/Invesco_QQQ" },
  GLD:  { name: "SPDR Gold Shares", blurb: "An ETF backed by physical gold bullion.", url: "https://en.wikipedia.org/wiki/SPDR_Gold_Shares" },
  SLV:  { name: "iShares Silver Trust", blurb: "An ETF backed by physical silver.", url: "https://en.wikipedia.org/wiki/Silver" },
  USO:  { name: "United States Oil Fund", blurb: "An ETF that tracks the price of crude oil.", url: "https://en.wikipedia.org/wiki/United_States_Oil_Fund" },
  ARKK: { name: "ARK Innovation ETF", blurb: "Actively-managed fund investing in disruptive, innovative companies.", url: "https://en.wikipedia.org/wiki/ARKK" },

  "GC=F": { name: "Gold Futures", blurb: "Precious metal, traditionally seen as a store of value.", url: "https://en.wikipedia.org/wiki/Gold" },
  "SI=F": { name: "Silver Futures", blurb: "Precious and industrial metal used in electronics and jewellery.", url: "https://en.wikipedia.org/wiki/Silver" },
  "CL=F": { name: "Crude Oil Futures", blurb: "West Texas Intermediate crude oil — a global oil price benchmark.", url: "https://en.wikipedia.org/wiki/Petroleum" },
  "NG=F": { name: "Natural Gas Futures", blurb: "Fossil fuel used for heating, electricity and industry.", url: "https://en.wikipedia.org/wiki/Natural_gas" },
}

export const API = "https://api.sentimentfx.org"

export const FX_TICKERS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]

export function _formatPrice(v, ticker) {
  if (v === null || v === undefined) return "—"
  if (FX_TICKERS.includes(ticker)) return v.toFixed(4)
  if (v >= 1000) return `£${(v / 1000).toFixed(2)}k`
  if (v >= 1)    return `£${v.toFixed(2)}`
  return `£${v.toFixed(4)}`
}

export const redirectToCheckout = async (priceId) => {
  const res = await fetch(`${API}/create-checkout-session?price_id=${priceId}`, {
    method: "POST",
  })
  const data = await res.json()
  window.location.href = data.url
}
