import { useState, useEffect } from "react"
import axios from "axios"
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer
} from "recharts"

const TICKERS = ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX", "LINK", "DOGE"]
const API = "https://crypto-sentiment-production.up.railway.app"

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap');

  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #080c10;
    --surface: #0d1117;
    --surface2: #161b22;
    --border: #21262d;
    --border2: #30363d;
    --text: #e6edf3;
    --muted: #7d8590;
    --accent: #f0b429;
    --accent2: #58a6ff;
    --positive: #3fb950;
    --negative: #f85149;
    --neutral: #8b949e;
    --mono: 'IBM Plex Mono', monospace;
    --sans: 'IBM Plex Sans', sans-serif;
  }

  body { background: var(--bg); }

  .dashboard {
    font-family: var(--sans);
    background: var(--bg);
    min-height: 100vh;
    color: var(--text);
    max-width: 1400px;
    margin: 0 auto;
    padding: 0;
  }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 24px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 100;
  }

  .topbar-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .logo {
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.15em;
    color: var(--accent);
    text-transform: uppercase;
  }

  .logo-divider {
    width: 1px;
    height: 20px;
    background: var(--border2);
  }

  .tagline {
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.05em;
    font-family: var(--mono);
  }

  .live-indicator {
    display: flex;
    align-items: center;
    gap: 6px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--positive);
    letter-spacing: 0.08em;
  }

  .live-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--positive);
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  .ticker-bar {
    display: flex;
    gap: 2px;
    padding: 12px 24px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    scrollbar-width: none;
  }

  .ticker-bar::-webkit-scrollbar { display: none; }

  .ticker-btn {
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.08em;
    padding: 6px 14px;
    border: 1px solid transparent;
    border-radius: 2px;
    cursor: pointer;
    background: transparent;
    color: var(--muted);
    transition: all 0.15s;
    white-space: nowrap;
  }

  .ticker-btn:hover {
    color: var(--text);
    border-color: var(--border2);
    background: var(--surface2);
  }

  .ticker-btn.active {
    color: var(--accent);
    border-color: var(--accent);
    background: rgba(240, 180, 41, 0.06);
  }

  .main {
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .stat-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
  }

  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 16px;
  }

  .stat-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  .stat-value {
    font-family: var(--mono);
    font-size: 22px;
    font-weight: 600;
    color: var(--text);
    line-height: 1;
  }

  .stat-sub {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    margin-top: 4px;
  }

  .positive-text { color: var(--positive); }
  .negative-text { color: var(--negative); }
  .neutral-text { color: var(--neutral); }
  .accent-text { color: var(--accent); }
  .accent2-text { color: var(--accent2); }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--surface2);
  }

  .panel-title {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.12em;
    color: var(--muted);
    text-transform: uppercase;
  }

  .panel-body {
    padding: 16px;
  }

  .panel-controls {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .control-divider {
    width: 1px;
    height: 16px;
    background: var(--border2);
  }

  .correlation-panel {
    border-left: 3px solid var(--accent);
  }

  .correlation-value {
    font-family: var(--mono);
    font-size: 32px;
    font-weight: 600;
    line-height: 1;
    margin-bottom: 8px;
  }

  .correlation-detail {
    font-size: 13px;
    color: var(--muted);
    line-height: 1.6;
  }

  .correlation-detail strong {
    color: var(--text);
    font-weight: 500;
  }

  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  .headlines-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
    background: var(--border);
  }

  .headline-item {
    background: var(--surface);
    padding: 12px 16px;
    display: flex;
    gap: 12px;
    align-items: flex-start;
    transition: background 0.1s;
  }

  .headline-item:hover {
    background: var(--surface2);
  }

  .sentiment-pill {
    font-family: var(--mono);
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    border-radius: 2px;
    white-space: nowrap;
    margin-top: 2px;
    flex-shrink: 0;
  }

  .pill-positive { background: rgba(63, 185, 80, 0.12); color: var(--positive); border: 1px solid rgba(63, 185, 80, 0.3); }
  .pill-negative { background: rgba(248, 81, 73, 0.12); color: var(--negative); border: 1px solid rgba(248, 81, 73, 0.3); }
  .pill-neutral  { background: rgba(139, 148, 158, 0.12); color: var(--neutral); border: 1px solid rgba(139, 148, 158, 0.3); }

  .headline-title {
    font-size: 12px;
    line-height: 1.5;
    color: var(--text);
    flex: 1;
  }

  .headline-score {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    white-space: nowrap;
    margin-top: 2px;
    flex-shrink: 0;
  }

  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 60px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.1em;
  }

  .loading::after {
    content: '';
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 1.5px solid var(--border2);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin-left: 10px;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .custom-tooltip {
    background: var(--surface2);
    border: 1px solid var(--border2);
    padding: 10px 14px;
    font-family: var(--mono);
    font-size: 11px;
    border-radius: 2px;
  }

  .tooltip-label {
    color: var(--muted);
    margin-bottom: 6px;
    font-size: 10px;
    letter-spacing: 0.06em;
  }

  .tooltip-row {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 2px;
  }

  .tooltip-key { color: var(--muted); }
  .tooltip-val { color: var(--text); font-weight: 500; }

  .ctrl-btn {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    padding: 4px 10px;
    border-radius: 2px;
    cursor: pointer;
    transition: all 0.15s;
  }

  @media (max-width: 768px) {
    .topbar { padding: 10px 16px; }
    .ticker-bar { padding: 10px 16px; }
    .main { padding: 12px 16px; gap: 12px; }
    .grid-2 { grid-template-columns: 1fr; }
    .stat-row { grid-template-columns: repeat(2, 1fr); }
    .tagline { display: none; }
    .logo-divider { display: none; }
  }
`

const CustomTooltip = ({ active, payload, label, symbol }) => {
  if (!active || !payload || !payload.length) return null
  return (
    <div className="custom-tooltip">
      <div className="tooltip-label">{label}</div>
      {payload.map((p, i) => (
        <div className="tooltip-row" key={i}>
          <span className="tooltip-key">{p.name}</span>
          <span className="tooltip-val" style={{ color: p.color }}>
            {p.name === "Price"
              ? `${symbol}${p.value?.toLocaleString()}`
              : p.value}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const [ticker, setTicker] = useState("BTC")
  const [allData, setAllData] = useState([])
  const [headlines, setHeadlines] = useState([])
  const [loading, setLoading] = useState(false)
  const [correlation, setCorrelation] = useState(null)
  const [stats, setStats] = useState(null)
  const [range, setRange] = useState(30)
  const [currency, setCurrency] = useState("GBP")
  const [gbpToUsd, setGbpToUsd] = useState(null)

  useEffect(() => {
    fetchDashboard()
  }, [ticker])

  useEffect(() => {
    axios.get(`${API}/stats`).then(r => setStats(r.data)).catch(() => {})
  }, [])

  useEffect(() => {
    fetch("https://api.exchangerate-api.com/v4/latest/GBP")
      .then(r => r.json())
      .then(d => setGbpToUsd(d.rates.USD))
      .catch(() => setGbpToUsd(1.27))
  }, [])

  const fetchDashboard = async () => {
    setLoading(true)
    try {
      const res = await axios.get(`${API}/dashboard/${ticker}`)
      const { sentiment, prices } = res.data

      const priceMap = {}
      prices.forEach(p => {
        const date = p.date.split("T")[0]
        priceMap[date] = p.close_price
      })

      const sentimentByDate = {}
      sentiment.forEach(s => {
        const date = s.date.split("T")[0]
        if (!sentimentByDate[date]) sentimentByDate[date] = []
        sentimentByDate[date].push(s.score)
      })

      const merged = Object.keys(sentimentByDate).map(date => ({
        date,
        sentiment: parseFloat((sentimentByDate[date].reduce((a, b) => a + b, 0) / sentimentByDate[date].length).toFixed(3)),
        price: priceMap[date] || null,
      })).sort((a, b) => new Date(a.date) - new Date(b.date))

      setAllData(merged)
      setHeadlines(sentiment.slice(0, 10))

      const corrRes = await axios.get(`${API}/correlation/${ticker}`)
      setCorrelation(corrRes.data)
    } catch (err) {
      console.error(err)
    }
    setLoading(false)
  }

  const filteredData = range === 999 ? allData : allData.slice(-range)

  const rate = currency === "USD" && gbpToUsd ? gbpToUsd : 1
  const symbol = currency === "USD" ? "$" : "£"

  const displayData = filteredData.map(d => ({
    ...d,
    price: d.price != null ? parseFloat((d.price * rate).toFixed(2)) : null,
  }))

  const avgSentiment = filteredData.length
    ? (filteredData.reduce((a, b) => a + b.sentiment, 0) / filteredData.length).toFixed(3)
    : null

  const sentimentSignal = avgSentiment
    ? avgSentiment > 0.1 ? "BULLISH" : avgSentiment < -0.1 ? "BEARISH" : "NEUTRAL"
    : null

  const latestPrice = displayData.length
    ? displayData[displayData.length - 1]?.price
    : null

  const priceDisplay = latestPrice != null
    ? `${symbol}${latestPrice >= 1000 ? latestPrice.toLocaleString() : latestPrice.toFixed(2)}`
    : "—"

  const rangeCtrlStyle = (r) => ({
    fontFamily: "var(--mono)",
    fontSize: "10px",
    letterSpacing: "0.08em",
    padding: "4px 10px",
    border: `1px solid ${range === r ? "var(--accent)" : "var(--border)"}`,
    borderRadius: "2px",
    cursor: "pointer",
    background: range === r ? "rgba(240,180,41,0.08)" : "transparent",
    color: range === r ? "var(--accent)" : "var(--muted)",
    transition: "all 0.15s",
  })

  const currencyCtrlStyle = (c) => ({
    fontFamily: "var(--mono)",
    fontSize: "10px",
    letterSpacing: "0.08em",
    padding: "4px 10px",
    border: `1px solid ${currency === c ? "var(--accent2)" : "var(--border)"}`,
    borderRadius: "2px",
    cursor: "pointer",
    background: currency === c ? "rgba(88,166,255,0.08)" : "transparent",
    color: currency === c ? "var(--accent2)" : "var(--muted)",
    transition: "all 0.15s",
  })

  const yAxisTickFormatter = v => {
    if (v >= 1000) return `${symbol}${(v / 1000).toFixed(0)}k`
    return `${symbol}${v.toFixed(2)}`
  }

  return (
    <>
      <style>{styles}</style>
      <div className="dashboard">
        <header className="topbar">
          <div className="topbar-left">
            <span className="logo">SentimentFX</span>
            <div className="logo-divider" />
            <span className="tagline">CRYPTO SENTIMENT INTELLIGENCE</span>
          </div>
          <div className="live-indicator">
            <div className="live-dot" />
            LIVE
          </div>
        </header>

        <nav className="ticker-bar">
          {TICKERS.map(t => (
            <button
              key={t}
              className={`ticker-btn ${ticker === t ? "active" : ""}`}
              onClick={() => setTicker(t)}
            >
              {t}
            </button>
          ))}
        </nav>

        <main className="main">
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-label">Asset</div>
              <div className="stat-value accent-text">{ticker}</div>
              <div className="stat-sub">Selected ticker</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Avg Sentiment</div>
              <div className={`stat-value ${avgSentiment > 0.1 ? "positive-text" : avgSentiment < -0.1 ? "negative-text" : "neutral-text"}`}>
                {avgSentiment ?? "—"}
              </div>
              <div className="stat-sub">{sentimentSignal ?? "Loading..."}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Latest Price</div>
              <div className="stat-value accent2-text">{priceDisplay}</div>
              <div className="stat-sub">
                {currency === "GBP" ? "British pound" : `USD · rate: ${gbpToUsd?.toFixed(4) ?? "..."}`}
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Correlation</div>
              <div className={`stat-value ${correlation?.correlation < 0 ? "negative-text" : "positive-text"}`}>
                {correlation?.correlation ?? "—"}
              </div>
              <div className="stat-sub">{correlation ? `${correlation.best_lag_days}d lag` : "Loading..."}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Total Headlines</div>
              <div className="stat-value">{stats?.total_headlines?.toLocaleString() ?? "—"}</div>
              <div className="stat-sub">{TICKERS.length} tickers tracked</div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">{ticker} / SENTIMENT vs PRICE ({currency})</span>
              <div className="panel-controls">
                {["GBP", "USD"].map(c => (
                  <button key={c} onClick={() => setCurrency(c)} style={currencyCtrlStyle(c)}>
                    {c}
                  </button>
                ))}
                <div className="control-divider" />
                {[7, 30, 90, 999].map(r => (
                  <button key={r} onClick={() => setRange(r)} style={rangeCtrlStyle(r)}>
                    {r === 999 ? "ALL" : `${r}D`}
                  </button>
                ))}
              </div>
            </div>
            <div className="panel-body">
              {loading ? (
                <div className="loading">FETCHING DATA</div>
              ) : (
                <ResponsiveContainer width="100%" height={320}>
                  <ComposedChart data={displayData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                    <CartesianGrid strokeDasharray="2 4" stroke="#21262d" vertical={false} />
                    <XAxis
                      dataKey="date"
                      stroke="#30363d"
                      tick={{ fill: "#7d8590", fontSize: 9, fontFamily: "IBM Plex Mono" }}
                      tickLine={false}
                      axisLine={{ stroke: "#21262d" }}
                    />
                    <YAxis
                      yAxisId="price"
                      orientation="right"
                      stroke="#30363d"
                      tick={{ fill: "#7d8590", fontSize: 9, fontFamily: "IBM Plex Mono" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={yAxisTickFormatter}
                    />
                    <YAxis
                      yAxisId="sentiment"
                      orientation="left"
                      domain={[-1, 1]}
                      stroke="#30363d"
                      tick={{ fill: "#7d8590", fontSize: 9, fontFamily: "IBM Plex Mono" }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <Tooltip content={<CustomTooltip symbol={symbol} />} />
                    <Legend
                      wrapperStyle={{ fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#7d8590", paddingTop: "12px" }}
                    />
                    <Bar
                      yAxisId="sentiment"
                      dataKey="sentiment"
                      name="Sentiment"
                      fill="#f0b429"
                      opacity={0.6}
                      radius={[1, 1, 0, 0]}
                    />
                    <Line
                      yAxisId="price"
                      type="monotone"
                      dataKey="price"
                      name="Price"
                      stroke="#58a6ff"
                      dot={false}
                      strokeWidth={1.5}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="grid-2">
            <div className="panel correlation-panel">
              <div className="panel-header">
                <span className="panel-title">PREDICTIVE SIGNAL</span>
              </div>
              <div className="panel-body">
                {correlation?.correlation !== undefined ? (
                  <>
                    <div className={`correlation-value ${correlation.correlation < 0 ? "negative-text" : "positive-text"}`}>
                      {(Math.abs(correlation.correlation) * 100).toFixed(0)}%
                    </div>
                    <div className="correlation-detail">
                      <strong>{correlation.interpretation}</strong>
                      <br /><br />
                      Signal type: <strong>{correlation.signal_type}</strong>
                      <br />
                      Best lag: <strong>{correlation.best_lag_days} day{correlation.best_lag_days !== 1 ? "s" : ""}</strong>
                      <br /><br />
                      {correlation.all_lags && (
                        <div style={{ marginTop: "8px" }}>
                          {Object.entries(correlation.all_lags).map(([lag, corr]) => (
                            <div key={lag} style={{
                              display: "flex",
                              justifyContent: "space-between",
                              padding: "3px 0",
                              borderBottom: "1px solid var(--border)",
                              fontFamily: "var(--mono)",
                              fontSize: "10px"
                            }}>
                              <span style={{ color: "var(--muted)" }}>{lag}d lag</span>
                              <span style={{
                                color: Math.abs(corr) > 0.3
                                  ? corr < 0 ? "var(--negative)" : "var(--positive)"
                                  : "var(--muted)"
                              }}>
                                {corr > 0 ? "+" : ""}{corr}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="loading">COMPUTING</div>
                )}
              </div>
            </div>

            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">LATEST HEADLINES</span>
                <span className="panel-title" style={{ color: "#7d8590" }}>{headlines.length} ITEMS</span>
              </div>
              <div className="headlines-list">
                {headlines.map((h, i) => (
                  <div className="headline-item" key={i}>
                    <div>
                      <span className={`sentiment-pill pill-${h.label}`}>
                        {h.label.toUpperCase()}
                      </span>
                    </div>
                    <div style={{ flex: 1 }}>
                      <div className="headline-title">{h.title}</div>
                    </div>
                    <div className={`headline-score ${h.score > 0.1 ? "positive-text" : h.score < -0.1 ? "negative-text" : "neutral-text"}`}>
                      {h.score > 0 ? "+" : ""}{h.score}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </main>
      </div>
    </>
  )
}