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
