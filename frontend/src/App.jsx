import { useState, useEffect } from "react"
import axios from "axios"
import { supabase } from "./supabaseClient"
import AuthModal from "./AuthModal"
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer
} from "recharts"

const TICKERS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
const FREE_TICKERS = ["BTC"]
const API = "https://api.sentimentfx.org"
const HEADLINES_PER_PAGE = 10

const redirectToCheckout = async (priceId) => {
  const res = await fetch(`${API}/create-checkout-session?price_id=${priceId}`, {
    method: "POST",
  })
  const data = await res.json()
  window.location.href = data.url
}

const exportData = async (type, ticker, session, days) => {
  try {
    const token = session?.access_token
    const url = `${API}/export/${type}/${ticker}${days ? `?days=${days}` : ""}`
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` }
    })
    if (!res.ok) throw new Error("Export failed")
    const blob = await res.blob()
    const objectUrl = window.URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = objectUrl
    a.download = `sentimentfx_${ticker.toLowerCase()}_${type}${days ? `_${days}d` : "_all"}.csv`
    a.click()
    window.URL.revokeObjectURL(objectUrl)
  } catch (e) {
    console.error("Export error:", e)
  }
}

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

  .topbar-right {
    display: flex;
    align-items: center;
    gap: 12px;
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

  .ticker-btn:hover:not(.locked) {
    color: var(--text);
    border-color: var(--border2);
    background: var(--surface2);
  }

  .ticker-btn.active {
    color: var(--accent);
    border-color: var(--accent);
    background: rgba(240, 180, 41, 0.06);
  }

  .ticker-btn.locked {
    opacity: 0.35;
    cursor: not-allowed;
  }

  .ticker-btn.locked::after {
    content: ' 🔒';
    font-size: 9px;
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

  .headline-item:hover { background: var(--surface2); }

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

  .pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    padding: 10px 16px;
    border-top: 1px solid var(--border);
    background: var(--surface2);
  }

  .page-btn {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.06em;
    padding: 4px 9px;
    border-radius: 2px;
    cursor: pointer;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    transition: all 0.15s;
    min-width: 28px;
    text-align: center;
  }

  .page-btn:hover:not(:disabled) {
    color: var(--text);
    border-color: var(--border2);
    background: var(--surface);
  }

  .page-btn.active {
    color: var(--accent2);
    border-color: var(--accent2);
    background: rgba(88, 166, 255, 0.08);
  }

  .page-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .upgrade-banner {
    background: rgba(240, 180, 41, 0.04);
    border: 1px solid rgba(240, 180, 41, 0.2);
    border-radius: 4px;
    padding: 16px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .upgrade-text {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.05em;
  }

  .upgrade-text strong {
    color: var(--accent);
    font-weight: 600;
  }

  .upgrade-btn {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    padding: 6px 16px;
    background: var(--accent);
    border: none;
    border-radius: 2px;
    color: #080c10;
    cursor: pointer;
    text-transform: uppercase;
    white-space: nowrap;
    transition: opacity 0.15s;
    flex-shrink: 0;
  }

  .upgrade-btn:hover { opacity: 0.85; }

  .tier-badge {
    font-family: var(--mono);
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.1em;
    padding: 2px 8px;
    border-radius: 2px;
    text-transform: uppercase;
  }

  .tier-free { background: rgba(139,148,158,0.12); color: var(--muted); border: 1px solid rgba(139,148,158,0.3); }
  .tier-pro { background: rgba(240,180,41,0.12); color: var(--accent); border: 1px solid rgba(240,180,41,0.3); }
  .tier-data { background: rgba(88,166,255,0.12); color: var(--accent2); border: 1px solid rgba(88,166,255,0.3); }

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

  .alert-select {
    font-family: var(--mono);
    font-size: 11px;
    padding: 6px 10px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 2px;
    color: var(--text);
    cursor: pointer;
  }

  .alert-input {
    font-family: var(--mono);
    font-size: 11px;
    padding: 6px 10px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 2px;
    color: var(--text);
    width: 80px;
  }

  .alert-section-label {
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.15em;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  .alerts-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
    background: var(--border);
  }

  .alert-item {
    background: var(--surface);
    padding: 10px 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  @media (max-width: 768px) {
    .topbar { padding: 10px 16px; }
    .ticker-bar { padding: 10px 16px; }
    .main { padding: 12px 16px; gap: 12px; }
    .grid-2 { grid-template-columns: 1fr; }
    .stat-row { grid-template-columns: repeat(2, 1fr); }
    .tagline { display: none; }
    .logo-divider { display: none; }
    .upgrade-banner { flex-direction: column; align-items: flex-start; }
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
            {p.name === "Price" ? `${symbol}${p.value?.toLocaleString()}` : p.value}
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
  const [headlinePage, setHeadlinePage] = useState(1)
  const [user, setUser] = useState(null)
  const [session, setSession] = useState(null)
  const [profile, setProfile] = useState(null)
  const [showAuth, setShowAuth] = useState(false)
  const [authMode, setAuthMode] = useState("login")
  const [alerts, setAlerts] = useState([])
  const [alertTicker, setAlertTicker] = useState("BTC")
  const [alertThreshold, setAlertThreshold] = useState(0.3)
  const [alertDirection, setAlertDirection] = useState("above")
  const [alertLoading, setAlertLoading] = useState(false)

  const urlParams = new URLSearchParams(window.location.search)
  const checkoutSuccess = urlParams.get("success")
  const checkoutCancelled = urlParams.get("cancelled")

  const isPro = profile?.tier === "pro" || profile?.tier === "data"
  const isData = profile?.tier === "data"
  const isLoggedIn = !!user

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const authParam = params.get("auth")
    if (authParam === "signup" || authParam === "login") {
      setAuthMode(authParam)
      setShowAuth(true)
      window.history.replaceState({}, "", window.location.pathname)
    }
    if (params.get("success") || params.get("cancelled")) {
      window.history.replaceState({}, "", window.location.pathname)
    }
  }, [])

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setUser(session?.user ?? null)
      setSession(session ?? null)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null)
      setSession(session ?? null)
    })
    return () => subscription.unsubscribe()
  }, [])

  useEffect(() => {
    if (!user) { setProfile(null); return }
    supabase
      .from("profiles")
      .select("*")
      .eq("id", user.id)
      .single()
      .then(({ data }) => setProfile(data))
  }, [user])

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

  useEffect(() => {
    if (isPro && session) fetchAlerts()
  }, [isPro, session])

  const fetchAlerts = async () => {
    if (!isPro || !session) return
    try {
      const res = await fetch(`${API}/alerts`, {
        headers: { Authorization: `Bearer ${session.access_token}` }
      })
      const data = await res.json()
      setAlerts(data)
    } catch (e) {
      console.error("Failed to fetch alerts:", e)
    }
  }

  const createAlert = async () => {
    setAlertLoading(true)
    try {
      await fetch(`${API}/alerts`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ticker: alertTicker,
          threshold: alertThreshold,
          direction: alertDirection
        })
      })
      await fetchAlerts()
    } catch (e) {
      console.error("Failed to create alert:", e)
    }
    setAlertLoading(false)
  }

  const deleteAlert = async (id) => {
    try {
      await fetch(`${API}/alerts/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${session.access_token}` }
      })
      await fetchAlerts()
    } catch (e) {
      console.error("Failed to delete alert:", e)
    }
  }

  const fetchDashboard = async () => {
    setLoading(true)
    setHeadlinePage(1)
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

      const allDates = new Set([
        ...Object.keys(sentimentByDate),
        ...Object.keys(priceMap),
      ])

      const merged = Array.from(allDates).map(date => ({
        date,
        sentiment: sentimentByDate[date]
          ? parseFloat(
              (
                sentimentByDate[date].reduce((a, b) => a + b, 0) /
                sentimentByDate[date].length
              ).toFixed(3)
            )
          : null,
        price: priceMap[date] || null,
      })).sort((a, b) => new Date(a.date) - new Date(b.date))

      setAllData(merged)
      setHeadlines(sentiment)

      const corrRes = await axios.get(`${API}/correlation/${ticker}`)
      setCorrelation(corrRes.data)
    } catch (err) {
      console.error(err)
    }
    setLoading(false)
  }

  const handleTickerClick = (t) => {
    const isLocked = !FREE_TICKERS.includes(t) && !isPro
    if (isLocked) { setAuthMode("signup"); setShowAuth(true); return }
    setTicker(t)
  }

  const filteredData = (() => {
    if (!isPro && range > 30) return allData.slice(-30)
    return range === 999 ? allData : allData.slice(-range)
  })()

  const rate = currency === "USD" && gbpToUsd ? gbpToUsd : 1
  const symbol = currency === "USD" ? "$" : "£"

  const displayData = filteredData.map(d => ({
    ...d,
    price: d.price != null ? parseFloat((d.price * rate).toFixed(2)) : null,
  }))

  const sentimentOnly = filteredData.filter(d => d.sentiment !== null && d.sentiment !== undefined)

  const avgSentiment = sentimentOnly.length
    ? (sentimentOnly.reduce((a, b) => a + b.sentiment, 0) / sentimentOnly.length).toFixed(3)
    : null

  const sentimentSignal = avgSentiment
    ? avgSentiment > 0.1 ? "BULLISH" : avgSentiment < -0.1 ? "BEARISH" : "NEUTRAL"
    : null

  const latestPrice = displayData.length ? displayData[displayData.length - 1]?.price : null
  const priceDisplay = latestPrice != null
    ? `${symbol}${latestPrice >= 1000 ? latestPrice.toLocaleString() : latestPrice.toFixed(2)}`
    : "—"

  const totalPages = Math.ceil(headlines.length / HEADLINES_PER_PAGE)
  const pagedHeadlines = headlines.slice(
    (headlinePage - 1) * HEADLINES_PER_PAGE,
    headlinePage * HEADLINES_PER_PAGE
  )

  const getPageNumbers = () => {
    if (totalPages <= 5) return Array.from({ length: totalPages }, (_, i) => i + 1)
    if (headlinePage <= 3) return [1, 2, 3, 4, "...", totalPages]
    if (headlinePage >= totalPages - 2) return [1, "...", totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
    return [1, "...", headlinePage - 1, headlinePage, headlinePage + 1, "...", totalPages]
  }

  const rangeCtrlStyle = (r) => ({
    fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
    padding: "4px 10px",
    border: `1px solid ${range === r ? "var(--accent)" : "var(--border)"}`,
    borderRadius: "2px",
    cursor: !isPro && r > 30 ? "not-allowed" : "pointer",
    background: range === r ? "rgba(240,180,41,0.08)" : "transparent",
    color: range === r ? "var(--accent)" : !isPro && r > 30 ? "var(--border2)" : "var(--muted)",
    transition: "all 0.15s",
    opacity: !isPro && r > 30 ? 0.4 : 1,
  })

  const currencyCtrlStyle = (c) => ({
    fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
    padding: "4px 10px",
    border: `1px solid ${currency === c ? "var(--accent2)" : "var(--border)"}`,
    borderRadius: "2px", cursor: "pointer",
    background: currency === c ? "rgba(88,166,255,0.08)" : "transparent",
    color: currency === c ? "var(--accent2)" : "var(--muted)",
    transition: "all 0.15s",
  })

  const yAxisTickFormatter = v => {
    if (v >= 1000) return `${symbol}${(v / 1000).toFixed(0)}k`
    return `${symbol}${v.toFixed(2)}`
  }

  const tierBadgeClass = profile?.tier === "pro"
    ? "tier-badge tier-pro"
    : profile?.tier === "data"
    ? "tier-badge tier-data"
    : "tier-badge tier-free"

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
          <div className="topbar-right">
            <a
              href="https://developers.sentimentfx.org"
              target="_blank"
              rel="noreferrer"
              style={{
                fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                color: "var(--muted)", textDecoration: "none", transition: "color 0.15s"
              }}
              onMouseOver={e => e.currentTarget.style.color = "var(--text)"}
              onMouseOut={e => e.currentTarget.style.color = "var(--muted)"}
            >
              DEVELOPERS
            </a>
            {user ? (
              <>
                <span className={tierBadgeClass}>{profile?.tier ?? "free"}</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)" }}>
                  {user.email}
                </span>
                <button
                  onClick={() => supabase.auth.signOut()}
                  style={{
                    fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                    padding: "4px 10px", border: "1px solid var(--border)", borderRadius: "2px",
                    cursor: "pointer", background: "transparent", color: "var(--muted)",
                  }}
                >
                  SIGN OUT
                </button>
              </>
            ) : (
              <button
                onClick={() => { setAuthMode("login"); setShowAuth(true) }}
                style={{
                  fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                  padding: "4px 10px", border: "1px solid var(--accent)", borderRadius: "2px",
                  cursor: "pointer", background: "rgba(240,180,41,0.06)", color: "var(--accent)",
                }}
              >
                SIGN IN
              </button>
            )}
            <div className="live-indicator">
              <div className="live-dot" />
              LIVE
            </div>
          </div>
        </header>

        <nav className="ticker-bar">
          {TICKERS.map(t => {
            const locked = !FREE_TICKERS.includes(t) && !isPro
            return (
              <button
                key={t}
                className={`ticker-btn ${ticker === t ? "active" : ""} ${locked ? "locked" : ""}`}
                onClick={() => handleTickerClick(t)}
              >
                {t}
              </button>
            )
          })}
        </nav>

        <main className="main">

          {!isLoggedIn && (
            <div className="upgrade-banner">
              <span className="upgrade-text">
                <strong>Free tier:</strong> BTC only · 30 day history · Sign in to unlock all 5 tickers, full history, API access and alerts.
              </span>
              <button className="upgrade-btn" onClick={() => { setAuthMode("signup"); setShowAuth(true) }}>
                Sign In / Sign Up
              </button>
            </div>
          )}

          {isLoggedIn && !isPro && (
            <div className="upgrade-banner">
              <span className="upgrade-text">
                <strong>Free tier:</strong> BTC only · 30 day history · Upgrade to Pro for all 5 tickers, full history, API access and alerts.
              </span>
              <div style={{ display: "flex", gap: "8px" }}>
                <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TMdhiRuGYgaTM3ycfIizjLQ")}>
                  £11.99 / mo
                </button>
                <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TMdlmRuGYgaTM3ysDve7yNI")}>
                  £99.99 / yr
                </button>
              </div>
            </div>
          )}

          {checkoutSuccess && (
            <div className="upgrade-banner" style={{ borderColor: "rgba(63,185,80,0.3)", background: "rgba(63,185,80,0.04)" }}>
              <span className="upgrade-text">
                <strong style={{ color: "var(--positive)" }}>Payment successful!</strong> Your Pro account is now active. Welcome aboard.
              </span>
            </div>
          )}

          {checkoutCancelled && (
            <div className="upgrade-banner" style={{ borderColor: "rgba(248,81,73,0.3)", background: "rgba(248,81,73,0.04)" }}>
              <span className="upgrade-text">
                <strong style={{ color: "var(--negative)" }}>Checkout cancelled.</strong> No charges were made.
              </span>
            </div>
          )}

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

          {isPro && (
            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end", alignItems: "center" }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", letterSpacing: "0.08em" }}>EXPORT</span>
              {[7, 30, 90, 0].map(d => (
                <button
                  key={d}
                  onClick={() => {
                    exportData("sentiment", ticker, session, d)
                    exportData("prices", ticker, session, d)
                  }}
                  style={{
                    fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                    padding: "6px 12px", border: "1px solid var(--border2)", borderRadius: "2px",
                    cursor: "pointer", background: "transparent", color: "var(--muted)",
                    transition: "all 0.15s",
                  }}
                  onMouseOver={e => { e.currentTarget.style.color = "var(--accent)"; e.currentTarget.style.borderColor = "var(--accent)" }}
                  onMouseOut={e => { e.currentTarget.style.color = "var(--muted)"; e.currentTarget.style.borderColor = "var(--border2)" }}
                >
                  {d === 0 ? "ALL" : `${d}D`}
                </button>
              ))}
            </div>
          )}

          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">{ticker} / SENTIMENT vs PRICE ({currency})</span>
              <div className="panel-controls">
                {["GBP", "USD"].map(c => (
                  <button key={c} onClick={() => setCurrency(c)} style={currencyCtrlStyle(c)}>{c}</button>
                ))}
                <div className="control-divider" />
                {[7, 30, 90, 999].map(r => (
                  <button
                    key={r}
                    onClick={() => {
                      if (!isPro && r > 30) { setAuthMode("signup"); setShowAuth(true); return }
                      setRange(r)
                    }}
                    style={rangeCtrlStyle(r)}
                  >
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
                    <Legend wrapperStyle={{ fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#7d8590", paddingTop: "12px" }} />
                    <Bar yAxisId="sentiment" dataKey="sentiment" name="Sentiment" fill="#f0b429" opacity={0.6} radius={[1, 1, 0, 0]} />
                    <Line yAxisId="price" type="monotone" dataKey="price" name="Price" stroke="#58a6ff" dot={false} strokeWidth={1.5} connectNulls={false} />
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
                              display: "flex", justifyContent: "space-between",
                              padding: "3px 0", borderBottom: "1px solid var(--border)",
                              fontFamily: "var(--mono)", fontSize: "10px"
                            }}>
                              <span style={{ color: "var(--muted)" }}>{lag}d lag</span>
                              <span style={{ color: Math.abs(corr) > 0.3 ? corr < 0 ? "var(--negative)" : "var(--positive)" : "var(--muted)" }}>
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
                <span className="panel-title" style={{ color: "#7d8590" }}>
                  {headlines.length} ITEMS · PG {headlinePage}/{totalPages || 1}
                </span>
              </div>
              <div className="headlines-list">
                {pagedHeadlines.map((h, i) => (
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
              {totalPages > 1 && (
                <div className="pagination">
                  <button className="page-btn" onClick={() => setHeadlinePage(p => p - 1)} disabled={headlinePage === 1}>&lt;</button>
                  {getPageNumbers().map((p, i) =>
                    p === "..." ? (
                      <span key={`ellipsis-${i}`} style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", padding: "0 4px" }}>...</span>
                    ) : (
                      <button key={p} className={`page-btn ${headlinePage === p ? "active" : ""}`} onClick={() => setHeadlinePage(p)}>{p}</button>
                    )
                  )}
                  <button className="page-btn" onClick={() => setHeadlinePage(p => p + 1)} disabled={headlinePage === totalPages}>&gt;</button>
                </div>
              )}
            </div>
          </div>

          {isPro && (
            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">SENTIMENT ALERTS</span>
                <span className="panel-title" style={{ color: "var(--muted)" }}>
                  {alerts.filter(a => a.active).length} ACTIVE
                </span>
              </div>
              <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <div>
                  <div className="alert-section-label">New alert</div>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                    <select className="alert-select" value={alertTicker} onChange={e => setAlertTicker(e.target.value)}>
                      {TICKERS.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <select className="alert-select" value={alertDirection} onChange={e => setAlertDirection(e.target.value)}>
                      <option value="above">Above</option>
                      <option value="below">Below</option>
                    </select>
                    <input
                      className="alert-input"
                      type="number"
                      min="-1"
                      max="1"
                      step="0.05"
                      value={alertThreshold}
                      onChange={e => setAlertThreshold(parseFloat(e.target.value))}
                    />
                    <button
                      onClick={createAlert}
                      disabled={alertLoading}
                      style={{
                        fontFamily: "var(--mono)", fontSize: "10px", fontWeight: 600,
                        letterSpacing: "0.1em", padding: "6px 16px", background: "var(--accent)",
                        border: "none", borderRadius: "2px", color: "#080c10",
                        cursor: "pointer", textTransform: "uppercase"
                      }}
                    >
                      {alertLoading ? "..." : "ADD ALERT"}
                    </button>
                  </div>
                </div>
                <div>
                  <div className="alert-section-label">Active alerts</div>
                  {alerts.length === 0 ? (
                    <div style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>
                      No alerts yet. Add one above.
                    </div>
                  ) : (
                    <div className="alerts-list">
                      {alerts.map(a => (
                        <div key={a.id} className="alert-item">
                          <span style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--text)" }}>
                            <span style={{ color: "var(--accent)" }}>{a.ticker}</span>
                            {" "}sentiment {a.direction}{" "}
                            <span style={{ color: a.direction === "above" ? "var(--positive)" : "var(--negative)" }}>
                              {a.threshold}
                            </span>
                          </span>
                          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                            <span style={{
                              fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.08em",
                              padding: "2px 8px", borderRadius: "2px",
                              background: a.active ? "rgba(63,185,80,0.1)" : "rgba(139,148,158,0.1)",
                              color: a.active ? "var(--positive)" : "var(--muted)",
                              border: `1px solid ${a.active ? "rgba(63,185,80,0.3)" : "rgba(139,148,158,0.3)"}`
                            }}>
                              {a.active ? "ACTIVE" : "FIRED"}
                            </span>
                            <button
                              onClick={() => deleteAlert(a.id)}
                              style={{
                                fontFamily: "var(--mono)", fontSize: "10px", padding: "3px 8px",
                                background: "transparent", border: "1px solid var(--border2)",
                                borderRadius: "2px", color: "var(--muted)", cursor: "pointer"
                              }}
                            >
                              ✕
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

        </main>
      </div>

      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialMode={authMode} />}
    </>
  )
}