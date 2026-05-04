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

// ─── Derived insight helpers ────────────────────────────────────────────────

/**
 * Returns sentiment trend data comparing the last 7 days vs the prior 7 days.
 * { current: number, previous: number, delta: number, direction: "up"|"down"|"flat" }
 */
function computeSentimentTrend(allData) {
  const withSentiment = allData
    .filter(d => d.sentiment !== null && d.sentiment !== undefined)
    .filter(d => Math.abs(d.sentiment) > 0.05)
  if (withSentiment.length < 2) return null

  const last7 = withSentiment.slice(-7)
  const prior7 = withSentiment.slice(-14, -7)

  if (last7.length === 0) return null

  const avg = arr => arr.reduce((a, b) => a + b.sentiment, 0) / arr.length
  const current = avg(last7)
  const previous = prior7.length > 0 ? avg(prior7) : null

  const delta = previous !== null ? current - previous : null
  const direction = delta === null ? "flat" : delta > 0.01 ? "up" : delta < -0.01 ? "down" : "flat"

  return {
    current: parseFloat(current.toFixed(3)),
    previous: previous !== null ? parseFloat(previous.toFixed(3)) : null,
    delta: delta !== null ? parseFloat(delta.toFixed(3)) : null,
    deltaPct: previous !== null && Math.abs(previous) > 0.05
      ? parseFloat(((delta / Math.abs(previous)) * 100).toFixed(1))
      : null,
    direction,
  }
}

/**
 * Builds the plain-English "Today's Signal" verdict.
 */
function buildTodaySignal({ ticker, avgSentiment, correlation, trend }) {
  if (avgSentiment === null || avgSentiment === undefined) return null

  const score = parseFloat(avgSentiment)
  const sentimentLabel = score > 0.3 ? "strongly bullish"
    : score > 0.1 ? "bullish"
    : score < -0.3 ? "strongly bearish"
    : score < -0.1 ? "bearish"
    : "neutral"

  const direction = score > 0.1 ? "BULLISH" : score < -0.1 ? "BEARISH" : "NEUTRAL"

  // Signal strength from correlation magnitude
  const corrValue = correlation?.correlation ? Math.abs(parseFloat(correlation.correlation)) : null
  const strength = corrValue === null ? null
    : corrValue >= 0.6 ? "strong"
    : corrValue >= 0.35 ? "moderate"
    : "weak"

  const lagDays = correlation?.best_lag_days ?? null
  const isMomentum = correlation?.signal_type?.includes("momentum")

  // Build the narrative sentence
  let narrative = `${ticker} sentiment is currently ${sentimentLabel} (${score > 0 ? "+" : ""}${score}).`

  if (lagDays !== null && strength !== null) {
    const followVerb = isMomentum ? "tends to follow" : "historically moves opposite to"
    narrative += ` Based on historical patterns, price ${followVerb} sentiment`
    if (lagDays === 0) {
      narrative += ` on the same day.`
    } else if (lagDays === 1) {
      narrative += ` within 1 day.`
    } else {
      narrative += ` within ${lagDays} days.`
    }
  }

  if (trend?.direction !== "flat" && trend?.deltaPct !== null) {
    const trendWord = trend.direction === "up" ? "improving" : "deteriorating"
    narrative += ` Sentiment has been ${trendWord} over the past week (${trend.direction === "up" ? "+" : ""}${trend.deltaPct}%).`
  }

  return { direction, sentimentLabel, score, strength, lagDays, isMomentum, narrative }
}

// ─── Strength meter bar ────────────────────────────────────────────────────

function StrengthMeter({ strength }) {
  const levels = ["weak", "moderate", "strong"]
  const idx = levels.indexOf(strength)
  const colors = ["#f85149", "#f0b429", "#3fb950"]
  return (
    <div style={{ display: "flex", gap: "3px", alignItems: "center" }}>
      {levels.map((l, i) => (
        <div
          key={l}
          style={{
            width: "28px", height: "4px", borderRadius: "2px",
            background: i <= idx ? colors[idx] : "var(--border2)",
            transition: "background 0.3s",
          }}
        />
      ))}
      <span style={{
        fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em",
        color: colors[idx], textTransform: "uppercase", marginLeft: "4px"
      }}>
        {strength}
      </span>
    </div>
  )
}

// ─── Trend Arrow ───────────────────────────────────────────────────────────

function TrendArrow({ trend }) {
  if (!trend) return <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)" }}>—</span>

  const { direction, deltaPct, delta } = trend

  const arrowMap = { up: "↑", down: "↓", flat: "→" }
  const colorMap = { up: "var(--positive)", down: "var(--negative)", flat: "var(--neutral)" }
  const labelMap = { up: "improving", down: "worsening", flat: "stable" }

  const deltaDisplay = deltaPct !== null
    ? `${direction === "up" ? "+" : ""}${deltaPct}%`
    : delta !== null
     ? `${delta > 0 ? "+" : ""}${delta}`
     : null

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
        <span style={{
          fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600,
          color: colorMap[direction], lineHeight: 1,
        }}>
          {arrowMap[direction]}
        </span>
        {deltaDisplay && (
          <span style={{
            fontFamily: "var(--mono)", fontSize: "13px", fontWeight: 600,
            color: colorMap[direction],
          }}>
            {deltaDisplay}
          </span>
        )}
      </div>
      <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", letterSpacing: "0.05em" }}>
        {labelMap[direction]} vs prior 7d
      </span>
    </div>
  )
}

// ─── Today's Signal Card ───────────────────────────────────────────────────

function TodaysSignalCard({ signal, trend, loading }) {
  if (loading) {
    return (
      <div style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "4px",
        padding: "20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      }}>
        <div className="skeleton" style={{ height: "12px", width: "120px", borderRadius: "2px" }} />
        <div className="skeleton" style={{ height: "28px", width: "200px", borderRadius: "2px" }} />
        <div className="skeleton" style={{ height: "40px", width: "90%", borderRadius: "2px" }} />
      </div>
    )
  }

  if (!signal) return null

  const directionColors = {
    BULLISH: { border: "rgba(63,185,80,0.4)", bg: "rgba(63,185,80,0.04)", text: "var(--positive)", badge: "rgba(63,185,80,0.12)", badgeBorder: "rgba(63,185,80,0.3)" },
    BEARISH: { border: "rgba(248,81,73,0.4)", bg: "rgba(248,81,73,0.04)", text: "var(--negative)", badge: "rgba(248,81,73,0.12)", badgeBorder: "rgba(248,81,73,0.3)" },
    NEUTRAL: { border: "rgba(139,148,158,0.4)", bg: "rgba(139,148,158,0.04)", text: "var(--neutral)", badge: "rgba(139,148,158,0.12)", badgeBorder: "rgba(139,148,158,0.3)" },
  }
  const dc = directionColors[signal.direction]

  return (
    <div style={{
      background: dc.bg,
      border: `1px solid ${dc.border}`,
      borderLeft: `4px solid ${dc.text}`,
      borderRadius: "4px",
      padding: "20px 24px",
      display: "flex",
      flexDirection: "column",
      gap: "14px",
    }}>
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.15em", color: "var(--muted)", textTransform: "uppercase" }}>
            TODAY'S SIGNAL
          </span>
          <span style={{
            fontFamily: "var(--mono)", fontSize: "10px", fontWeight: 700,
            letterSpacing: "0.12em", padding: "3px 10px", borderRadius: "2px",
            background: dc.badge, color: dc.text, border: `1px solid ${dc.badgeBorder}`,
          }}>
            {signal.direction}
          </span>
          {signal.isMomentum !== null && (
            <span style={{
              fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.08em",
              padding: "2px 8px", borderRadius: "2px",
              background: "var(--surface2)", color: "var(--muted)",
              border: "1px solid var(--border2)",
            }}>
              {signal.isMomentum ? "MOMENTUM" : "CONTRARIAN"}
            </span>
          )}
        </div>
        {signal.strength && <StrengthMeter strength={signal.strength} />}
      </div>

      {/* Narrative */}
      <p style={{
        fontFamily: "var(--sans)", fontSize: "14px", lineHeight: "1.65",
        color: "var(--text)", margin: 0,
      }}>
        {signal.narrative}
      </p>

      {/* Bottom row: score + trend + lag */}
      <div style={{ display: "flex", gap: "24px", flexWrap: "wrap", alignItems: "flex-start", borderTop: "1px solid var(--border)", paddingTop: "14px" }}>
        <div>
          <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
            Sentiment Score
          </div>
          <div style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600, color: dc.text, lineHeight: 1 }}>
            {signal.score > 0 ? "+" : ""}{signal.score}
          </div>
          <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "3px" }}>
            range: −1.0 to +1.0
          </div>
        </div>

        <div style={{ width: "1px", background: "var(--border)", alignSelf: "stretch" }} />

        <div>
          <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
            7-Day Trend
          </div>
          <TrendArrow trend={trend} />
        </div>

        {signal.lagDays !== null && (
          <>
            <div style={{ width: "1px", background: "var(--border)", alignSelf: "stretch" }} />
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
                Price Lag
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600, color: "var(--accent2)", lineHeight: 1 }}>
                {signal.lagDays}d
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "3px" }}>
                predicted lead time
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
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
    grid-template-columns: repeat(5, 1fr);
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

  /* Skeleton */
  @keyframes shimmer {
    0% { background-position: -600px 0; }
    100% { background-position: 600px 0; }
  }

  .skeleton {
    background: linear-gradient(90deg, var(--surface2) 25%, var(--border) 50%, var(--surface2) 75%);
    background-size: 600px 100%;
    animation: shimmer 1.4s infinite;
    border-radius: 2px;
  }

  .skeleton-headline {
    padding: 12px 16px;
    display: flex;
    gap: 12px;
    align-items: flex-start;
    border-bottom: 1px solid var(--border);
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

  .explainer-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
  }

  .explainer-card { padding-left: 12px; }

  .explainer-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 6px;
  }

  .explainer-text {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.7;
  }

  .disclaimer {
    background: rgba(240,180,41,0.04);
    border: 1px solid rgba(240,180,41,0.15);
    border-radius: 2px;
    padding: 10px 14px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    line-height: 1.6;
  }

  /* Heatmap */
  .heatmap-grid {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .heatmap-months {
    display: flex;
    gap: 4px;
    margin-bottom: 4px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .heatmap-months::-webkit-scrollbar { display: none; }

  .heatmap-week {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex-shrink: 0;
  }

  .heatmap-cell {
    width: 14px;
    height: 14px;
    border-radius: 2px;
    cursor: pointer;
    transition: transform 0.1s, opacity 0.1s;
    position: relative;
    flex-shrink: 0;
  }

  .heatmap-cell:hover {
    transform: scale(1.3);
    z-index: 10;
  }

  .heatmap-cell.empty {
    background: transparent;
    cursor: default;
  }

  .heatmap-cell.no-data {
    background: var(--surface2);
    border: 1px solid var(--border);
  }

  .heatmap-cell.selected {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }

  .heatmap-legend {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 12px;
  }

  .heatmap-legend-label {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    letter-spacing: 0.08em;
  }

  .heatmap-legend-cells {
    display: flex;
    gap: 3px;
    align-items: center;
  }

  .heatmap-legend-cell {
    width: 12px;
    height: 12px;
    border-radius: 2px;
  }

  .heatmap-day-detail {
    margin-top: 16px;
    border-top: 1px solid var(--border);
    padding-top: 16px;
  }

  .heatmap-day-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }

  .heatmap-day-date {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text);
    letter-spacing: 0.08em;
  }

  .heatmap-day-score {
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 600;
  }

  .heatmap-day-headlines {
    display: flex;
    flex-direction: column;
    gap: 1px;
    background: var(--border);
    max-height: 200px;
    overflow-y: auto;
  }

  .heatmap-day-headline {
    background: var(--surface);
    padding: 8px 12px;
    display: flex;
    gap: 10px;
    align-items: flex-start;
  }

  .dow-labels {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-right: 4px;
    flex-shrink: 0;
  }

  .dow-label {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    height: 14px;
    display: flex;
    align-items: center;
    letter-spacing: 0.05em;
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

const ChartSkeleton = () => (
  <div style={{ padding: "16px" }}>
    <div className="skeleton" style={{ height: "320px", width: "100%" }} />
  </div>
)

const CorrelationSkeleton = () => (
  <div style={{ padding: "16px", display: "flex", flexDirection: "column", gap: "12px" }}>
    <div className="skeleton" style={{ height: "48px", width: "80px", borderRadius: "2px" }} />
    <div className="skeleton" style={{ height: "12px", width: "90%", borderRadius: "2px" }} />
    <div className="skeleton" style={{ height: "12px", width: "70%", borderRadius: "2px" }} />
    <div className="skeleton" style={{ height: "12px", width: "80%", borderRadius: "2px" }} />
    <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "8px" }}>
      {[...Array(6)].map((_, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", paddingBottom: "8px", borderBottom: "1px solid var(--border)" }}>
          <div className="skeleton" style={{ height: "10px", width: "40px", borderRadius: "2px" }} />
          <div className="skeleton" style={{ height: "10px", width: "32px", borderRadius: "2px" }} />
        </div>
      ))}
    </div>
  </div>
)

const HeadlinesSkeleton = () => (
  <div>
    {[...Array(HEADLINES_PER_PAGE)].map((_, i) => (
      <div key={i} className="skeleton-headline">
        <div className="skeleton" style={{ height: "18px", width: "52px", flexShrink: 0, borderRadius: "2px" }} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "6px" }}>
          <div className="skeleton" style={{ height: "12px", width: "100%", borderRadius: "2px" }} />
          <div className="skeleton" style={{ height: "12px", width: "65%", borderRadius: "2px" }} />
        </div>
        <div className="skeleton" style={{ height: "10px", width: "32px", flexShrink: 0, borderRadius: "2px" }} />
      </div>
    ))}
  </div>
)

function sentimentColor(score) {
  if (score === null || score === undefined) return null
  if (score > 0.3) return `rgba(63,185,80,0.85)`
  if (score > 0.1) return `rgba(63,185,80,0.45)`
  if (score > -0.1) return `rgba(139,148,158,0.4)`
  if (score > -0.3) return `rgba(248,81,73,0.45)`
  return `rgba(248,81,73,0.85)`
}

function SentimentHeatmap({ allData, headlines, isPro, onUpgrade }) {
  const [selectedDay, setSelectedDay] = useState(null)

  const sentimentByDate = {}
  allData.forEach(d => {
    if (d.sentiment !== null && d.sentiment !== undefined) {
      sentimentByDate[d.date] = d.sentiment
    }
  })

  const headlinesByDate = {}
  headlines.forEach(h => {
    const date = h.date.split("T")[0]
    if (!headlinesByDate[date]) headlinesByDate[date] = []
    headlinesByDate[date].push(h)
  })

  const today = new Date()
  today.setHours(0, 0, 0, 0)

  const startDate = isPro
  ? new Date(allData.find(d => d.sentiment !== null)?.date ?? today)
  : (() => { const d = new Date(today); d.setDate(d.getDate() - 30); return d })()

  const calStart = new Date(startDate)
  calStart.setDate(calStart.getDate() - calStart.getDay())

  const weeks = []
  let current = new Date(calStart)
  while (current <= today) {
    const week = []
    for (let d = 0; d < 7; d++) {
      const dateStr = current.toISOString().split("T")[0]
      const isInRange = current >= startDate && current <= today
      week.push({
        date: dateStr,
        inRange: isInRange,
        sentiment: isInRange ? (sentimentByDate[dateStr] ?? null) : null,
        day: current.getDay(),
        month: current.getMonth(),
        dayOfMonth: current.getDate(),
      })
      current = new Date(current)
      current.setDate(current.getDate() + 1)
    }
    weeks.push(week)
  }

  const monthLabels = []
  weeks.forEach((week, wi) => {
    const firstInRange = week.find(d => d.inRange)
    if (firstInRange && firstInRange.dayOfMonth <= 7) {
      const monthNames = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
      monthLabels.push({ weekIndex: wi, label: monthNames[firstInRange.month] })
    }
  })

  const selectedHeadlines = selectedDay ? (headlinesByDate[selectedDay] || []) : []
  const selectedScore = selectedDay ? sentimentByDate[selectedDay] : null

  return (
  <div style={{ overflowX: "auto", paddingBottom: "16px" }}>
    <div style={{ display: "flex", gap: "0", minWidth: "max-content" }}>
      <div className="dow-labels" style={{ marginTop: "20px" }}>
        {["S","M","T","W","T","F","S"].map((d, i) => (
          <div key={i} className="dow-label">{i % 2 === 1 ? d : ""}</div>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
        <div style={{ display: "flex", gap: "4px", height: "16px", position: "relative" }}>
          {weeks.map((_, wi) => {
            const label = monthLabels.find(m => m.weekIndex === wi)
            return (
              <div key={wi} style={{ width: "14px", flexShrink: 0, position: "relative" }}>
                {label && (
                  <span style={{
                    position: "absolute", left: 0,
                    fontFamily: "var(--mono)", fontSize: "9px",
                    color: "var(--muted)", whiteSpace: "nowrap", letterSpacing: "0.05em"
                  }}>
                    {label.label}
                  </span>
                )}
              </div>
            )
          })}
        </div>

        <div style={{ display: "flex", gap: "4px" }}>
          {weeks.map((week, wi) => (
            <div key={wi} className="heatmap-week">
              {week.map((day, di) => {
                if (!day.inRange) {
                  return <div key={di} className="heatmap-cell empty" />
                }
                const color = sentimentColor(day.sentiment)
                return (
                  <div
                    key={di}
                    className={`heatmap-cell ${color ? "" : "no-data"} ${selectedDay === day.date ? "selected" : ""}`}
                    style={color ? { background: color } : {}}
                    onClick={() => setSelectedDay(selectedDay === day.date ? null : day.date)}
                    title={`${day.date}${day.sentiment !== null ? ` · ${day.sentiment > 0 ? "+" : ""}${day.sentiment}` : " · no data"}`}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </div>

    <div className="heatmap-legend">
      <span className="heatmap-legend-label">BEARISH</span>
      <div className="heatmap-legend-cells">
        {[
          "rgba(248,81,73,0.85)",
          "rgba(248,81,73,0.45)",
          "rgba(139,148,158,0.4)",
          "rgba(63,185,80,0.45)",
          "rgba(63,185,80,0.85)",
        ].map((c, i) => (
          <div key={i} className="heatmap-legend-cell" style={{ background: c }} />
        ))}
      </div>
      <span className="heatmap-legend-label">BULLISH</span>
      <div className="heatmap-legend-cell no-data" style={{ background: "var(--surface2)", border: "1px solid var(--border)", width: "12px", height: "12px", borderRadius: "2px", marginLeft: "8px" }} />
      <span className="heatmap-legend-label">NO DATA</span>
    </div>

    {!isPro && (
      <div style={{
        marginTop: "12px", padding: "8px 12px",
        background: "rgba(240,180,41,0.04)", border: "1px solid rgba(240,180,41,0.15)",
        borderRadius: "2px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px"
      }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)" }}>
          Showing 30 days · <span style={{ color: "var(--accent)" }}>Upgrade to Pro</span> for full history
        </span>
        <button
          onClick={onUpgrade}
          style={{
            fontFamily: "var(--mono)", fontSize: "9px", fontWeight: 600,
            letterSpacing: "0.1em", padding: "4px 10px", background: "var(--accent)",
            border: "none", borderRadius: "2px", color: "#080c10",
            cursor: "pointer", textTransform: "uppercase", whiteSpace: "nowrap"
          }}
        >
          Upgrade
        </button>
      </div>
    )}

    {selectedDay && (
      <div className="heatmap-day-detail">
        <div className="heatmap-day-header">
          <span className="heatmap-day-date">{selectedDay}</span>
          {selectedScore !== null && selectedScore !== undefined ? (
            <span className="heatmap-day-score" style={{ color: selectedScore > 0.1 ? "var(--positive)" : selectedScore < -0.1 ? "var(--negative)" : "var(--neutral)" }}>
              {selectedScore > 0 ? "+" : ""}{selectedScore}
            </span>
          ) : (
            <span style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>No sentiment data</span>
          )}
          <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)" }}>
            {selectedHeadlines.length} headline{selectedHeadlines.length !== 1 ? "s" : ""}
          </span>
        </div>
        {selectedHeadlines.length > 0 ? (
          <div className="heatmap-day-headlines">
            {selectedHeadlines.map((h, i) => (
              <div key={i} className="heatmap-day-headline">
                <span className={`sentiment-pill pill-${h.label}`} style={{ marginTop: 0 }}>
                  {h.label.toUpperCase()}
                </span>
                <span style={{ fontSize: "12px", color: "var(--text)", flex: 1, lineHeight: "1.5" }}>{h.title}</span>
                <span className={`headline-score ${h.score > 0.1 ? "positive-text" : h.score < -0.1 ? "negative-text" : "neutral-text"}`}>
                  {h.score > 0 ? "+" : ""}{h.score}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>
            No headlines for this day.
          </div>
        )}
      </div>
    )}
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

  // ── Derived insight state ──────────────────────────────────────────────────
  const sentimentTrend = loading ? null : computeSentimentTrend(allData)

  const sentimentOnly = (range === 999 ? allData : allData.slice(-range))
    .filter(d => d.sentiment !== null && d.sentiment !== undefined)
    .filter(d => Math.abs(d.sentiment) > 0.05)

  const avgSentiment = sentimentOnly.length
    ? parseFloat((sentimentOnly.reduce((a, b) => a + b.sentiment, 0) / sentimentOnly.length).toFixed(3))
    : null

  const todaySignal = (loading || avgSentiment === null)
    ? null
    : buildTodaySignal({ ticker, avgSentiment, correlation, trend: sentimentTrend })

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
    fetchDashboard(range, 1, ticker)
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

  const fetchDashboard = async (selectedRange = range, headlinePageNum = 1, selectedTicker = ticker) => {
    setLoading(true)
    if (headlinePageNum === 1) setHeadlinePage(1)
    try {
      const isAll = selectedRange === 999
      const days = isAll ? 90 : selectedRange

      const baseUrl = isAll && isPro
        ? `${API}/dashboard/${selectedTicker}?all=true`
        : `${API}/dashboard/${selectedTicker}?days=${Math.max(days, 90)}`

      const url = headlinePageNum === 1
        ? `${baseUrl}&page=1&limit=50`
        : `${baseUrl}&page=${headlinePageNum}&limit=50`

      const [dashRes, sentimentRes] = await Promise.all([
        axios.get(url),
        headlinePageNum === 1
          ? axios.get(`${API}/sentiment-summary/${selectedTicker}?${isAll && isPro ? "all=true" : `days=${Math.max(days, 90)}`}`)
          : Promise.resolve(null)
      ])

      const { sentiment, prices } = dashRes.data

      const priceMap = {}
      prices.forEach(p => {
        const date = p.date.split("T")[0]
        priceMap[date] = p.close_price
      })

      if (headlinePageNum === 1) {
        const summaryData = sentimentRes.data.data
        const sentimentMap = {}
        summaryData.forEach(s => { sentimentMap[s.date] = s.avg_sentiment })

        const allDates = new Set([...Object.keys(sentimentMap), ...Object.keys(priceMap),])

        const merged = Array.from(allDates).map(date => ({
          date,
          sentiment: sentimentMap[date] ?? null,
          price: priceMap[date] ?? null,
        })).sort((a, b) => new Date(a.date) - new Date(b.date))

        setAllData(merged)
        setHeadlines(sentiment)
      } else {
        setHeadlines(prev => [...prev, ...sentiment])
      }

      setCorrelation(null)
      const corrRes = await axios.get(`${API}/correlation/${selectedTicker}`)
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

  const sentimentSignal = avgSentiment !== null
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

          {/* ── TODAY'S SIGNAL CARD ─────────────────────────────────────────── */}
          <TodaysSignalCard signal={todaySignal} trend={sentimentTrend} loading={loading} />

          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-label">Asset</div>
              <div className="stat-value accent-text">{ticker}</div>
              <div className="stat-sub">Selected ticker</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Avg Sentiment</div>
              <div className={`stat-value ${!loading && avgSentiment > 0.1 ? "positive-text" : !loading && avgSentiment < -0.1 ? "negative-text" : "neutral-text"}`}>
                {loading
                  ? <span className="skeleton" style={{ display: "inline-block", width: "80px", height: "22px", borderRadius: "2px" }} />
                  : (avgSentiment ?? "—")}
              </div>
              <div className="stat-sub">{loading ? "—" : (sentimentSignal ?? "Loading...")}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Latest Price</div>
              <div className="stat-value accent2-text">
                {loading
                  ? <span className="skeleton" style={{ display: "inline-block", width: "100px", height: "22px", borderRadius: "2px" }} />
                  : priceDisplay}
              </div>
              <div className="stat-sub">
                {currency === "GBP" ? "British pound" : `USD · rate: ${gbpToUsd?.toFixed(4) ?? "..."}`}
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Correlation</div>
              <div className={`stat-value ${!loading && correlation?.correlation < 0 ? "negative-text" : "positive-text"}`}>
                {loading
                  ? <span className="skeleton" style={{ display: "inline-block", width: "60px", height: "22px", borderRadius: "2px" }} />
                  : (correlation?.correlation ?? "—")}
              </div>
              <div className="stat-sub">{loading ? "—" : (correlation ? `${correlation.best_lag_days}d lag` : "Loading...")}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">7d Trend</div>
              <div className="stat-value" style={{ fontSize: "18px" }}>
                {loading
                  ? <span className="skeleton" style={{ display: "inline-block", width: "60px", height: "22px", borderRadius: "2px" }} />
                  : sentimentTrend
                    ? <span style={{
                        color: sentimentTrend.direction === "up" ? "var(--positive)" : sentimentTrend.direction === "down" ? "var(--negative)" : "var(--neutral)"
                      }}>
                        {sentimentTrend.direction === "up" ? "↑" : sentimentTrend.direction === "down" ? "↓" : "→"}
                        {sentimentTrend.deltaPct !== null ? ` ${Math.abs(sentimentTrend.deltaPct)}%` : ""}
                      </span>
                    : "—"
                }
              </div>
              <div className="stat-sub">
                {sentimentTrend
                  ? sentimentTrend.direction === "up" ? "improving" : sentimentTrend.direction === "down" ? "worsening" : "stable"
                  : "vs prior 7d"}
              </div>
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
                      fetchDashboard(r)
                    }}
                    style={rangeCtrlStyle(r)}
                  >
                    {r === 999 ? "ALL" : `${r}D`}
                  </button>
                ))}
              </div>
            </div>
            {loading ? <ChartSkeleton /> : (
              <div className="panel-body" style={{ overflowX: "hidden" }}>
                <ResponsiveContainer width="100%" height={260}>
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
              </div>
            )}
          </div>

          <div className="grid-2">
            <div className="panel correlation-panel">
              <div className="panel-header">
                <span className="panel-title">PREDICTIVE SIGNAL</span>
              </div>
              {loading ? <CorrelationSkeleton /> : (
                <div className="panel-body">
                  {correlation?.correlation !== undefined ? (
                    <>
                      <div className="correlation-detail">
                        {/* ── SIGNAL SUMMARY (plain English) ───────────────────────── */}
                        {todaySignal && (
                          <div style={{
                            marginBottom: "16px",
                            padding: "12px 14px",
                            background: "var(--surface2)",
                            border: "1px solid var(--border)",
                            borderRadius: "2px",
                            fontFamily: "var(--sans)",
                            fontSize: "13px",
                            color: "var(--text)",
                            lineHeight: "1.65",
                          }}>
                            {todaySignal.narrative}
                            {todaySignal.strength && (
                              <div style={{ marginTop: "10px" }}>
                                <StrengthMeter strength={todaySignal.strength} />
                              </div>
                            )}
                          </div>
                        )}

                        <strong>{correlation.interpretation}</strong>
                        <br /><br />
                        <div style={{ background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "2px", padding: "10px 14px", marginBottom: "12px" }}>
                          <div style={{ marginBottom: "8px" }}>
                            <span style={{ color: "var(--muted)" }}>Signal: </span>
                            <strong style={{ color: correlation.signal_type?.includes("momentum") ? "var(--positive)" : "var(--negative)" }}>
                              {correlation.signal_type?.includes("momentum") ? "📈 Momentum" : "📉 Contrarian"}
                            </strong>
                            <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "3px" }}>
                              {correlation.signal_type?.includes("momentum")
                                ? "Positive news tends to be followed by price rises"
                                : "Positive news tends to be followed by price drops — market may have already priced it in"}
                            </div>
                          </div>
                          <div>
                            <span style={{ color: "var(--muted)" }}>Best lag: </span>
                            <strong>{correlation.best_lag_days} day{correlation.best_lag_days !== 1 ? "s" : ""}</strong>
                            <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "3px" }}>
                              Sentiment today predicts price movement {correlation.best_lag_days} day{correlation.best_lag_days !== 1 ? "s" : ""} from now
                            </div>
                          </div>
                        </div>
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
                        <div style={{
                          marginTop: "16px",
                          padding: "10px 14px",
                          background: "var(--surface2)",
                          border: "1px solid var(--border)",
                          borderRadius: "2px",
                          fontFamily: "var(--mono)",
                          fontSize: "11px",
                          color: "var(--muted)",
                          lineHeight: "1.6"
                        }}>
                          💡 A higher correlation at longer lags suggests sentiment is a leading indicator — news today may move prices in {correlation.best_lag_days} day{correlation.best_lag_days !== 1 ? "s" : ""}.
                        </div>
                      </div>
                    </>
                  ) : (
                    <div style={{ padding: "16px", fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>
                      Not enough data yet.
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="panel" style={{ display: "none" }}>
            <div className="panel-header">
              <span className="panel-title">SENTIMENT HEATMAP</span>
              <span className="panel-title" style={{ color: "var(--muted)" }}>
                {isPro ? "FULL HISTORY" : "30 DAYS"}
              </span>
            </div>
            <div className="panel-body">
              {loading ? (
                <div className="skeleton" style={{ height: "120px", width: "100%" }} />
              ) : (
                <SentimentHeatmap
                  allData={allData}
                  headlines={headlines}
                  isPro={isPro}
                  onUpgrade={() => { setAuthMode("signup"); setShowAuth(true) }}
                />
              )}
            </div>
          </div>

            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">LATEST HEADLINES</span>
                <span className="panel-title" style={{ color: "#7d8590" }}>
                  {loading ? "—" : `${headlines.length} ITEMS · PG ${headlinePage}/${totalPages || 1}`}
                </span>
              </div>
              {loading ? <HeadlinesSkeleton /> : (
                <>
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
                      <button 
                        className="page-btn" 
                        onClick={() => {
                          const nextPage = headlinePage + 1
                          setHeadlinePage(nextPage)
                          if (nextPage * HEADLINES_PER_PAGE > headlines.length) {
                            const apiPage = Math.ceil(headlines.length / 50) + 1
                            fetchDashboard(range, apiPage)
                          }
                        }}
                        disabled={headlinePage === totalPages}
                      >&gt;</button>
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
                </>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">UNDERSTANDING THE SIGNAL</span>
            </div>
            <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div className="explainer-grid">
                <div className="explainer-card" style={{ borderLeft: "2px solid var(--accent)" }}>
                  <div className="explainer-label" style={{ color: "var(--accent)" }}>What is Sentiment?</div>
                  <div className="explainer-text">
                    Each news headline is scored from <span style={{ color: "var(--positive)" }}>+1 (very positive)</span> to <span style={{ color: "var(--negative)" }}>-1 (very negative)</span> using an AI model trained on financial news. The average of all recent headlines gives the overall sentiment score.
                  </div>
                </div>
                <div className="explainer-card" style={{ borderLeft: "2px solid var(--accent2)" }}>
                  <div className="explainer-label" style={{ color: "var(--accent2)" }}>What is Correlation?</div>
                  <div className="explainer-text">
                    Correlation measures how closely sentiment and price move together. <span style={{ color: "var(--text)" }}>100%</span> means they move in perfect sync. <span style={{ color: "var(--text)" }}>0%</span> means no relationship. A negative value means they move in opposite directions.
                  </div>
                </div>
                <div className="explainer-card" style={{ borderLeft: "2px solid var(--positive)" }}>
                  <div className="explainer-label" style={{ color: "var(--positive)" }}>What is Lag?</div>
                  <div className="explainer-text">
                    Lag is the delay between a sentiment shift and a price move. A <span style={{ color: "var(--text)" }}>2 day lag</span> means sentiment today tends to predict where the price goes in 2 days — giving you a potential early signal.
                  </div>
                </div>
                <div className="explainer-card" style={{ borderLeft: "2px solid var(--neutral)" }}>
                  <div className="explainer-label" style={{ color: "var(--neutral)" }}>Momentum vs Contrarian</div>
                  <div className="explainer-text">
                    <span style={{ color: "var(--positive)" }}>Momentum</span> means positive news tends to be followed by price rises. <span style={{ color: "var(--negative)" }}>Contrarian</span> means positive news is followed by price drops — the market may already have priced it in.
                  </div>
                </div>
              </div>
              <div className="disclaimer">
                ⚠ Correlation is not causation. This data is for informational purposes only and should not be used as financial advice.
              </div>
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