import { useState, useEffect } from "react"
import axios from "axios"
import { supabase } from "./supabaseClient"
import AuthModal from "./AuthModal"
import AccountModal from "./AccountModal"
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from "recharts"

const CATEGORIES = {
  Crypto: ["BTC", "ETH", "SOL", "XRP", "DOGE"],
  FX: ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"],
  Stocks: ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "BAC", "GS", "V", "MA", "XOM", "JNJ", "AMD", "NFLX", "WMT", "UBER", "CRM", "PLTR"],
  ETFs: ["SPY", "QQQ", "GLD", "SLV", "USO", "ARKK"],
  Commodities: ["GC=F", "SI=F", "CL=F", "NG=F"],
}
const TICKERS = Object.values(CATEGORIES).flat()
const FREE_TICKERS = ["BTC"]
const FX_TICKERS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
const FX_LABELS = { EURUSD: "EUR/USD", GBPUSD: "GBP/USD", USDJPY: "USD/JPY", AUDUSD: "AUD/USD", USDCAD: "USD/CAD", USDCHF: "USD/CHF", NZDUSD: "NZD/USD" }
const COMMODITY_LABELS = { "GC=F": "Gold", "SI=F": "Silver", "CL=F": "Oil", "NG=F": "Nat Gas" }
// Mirror of landing/sentiment-tickers.json — kept in sync manually because the
// frontend bundles independently of the landing repo.  Used by the leaderboard
// row links to deep-link into the per-ticker SEO landing page on sentimentfx.org.
const TICKER_SLUGS = {
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

// ─── Correlation response helpers ──────────────────────────────────────────
// New API shape:
// {
//   ticker, window_days, sample_size,
//   primary_signal: { type, correlation, p_value, ci_95, strength, direction },
//   secondary_signals: { sentiment_level_vs_next_day_return: {...}, news_volume_vs_next_day_return: {...} },
//   baseline: { momentum_autocorrelation, momentum_p_value, primary_beats_momentum },
//   interpretation
// }
// Returns null if "message" present (not enough data).

function getPrimary(correlation) {
  return correlation?.primary_signal ?? null
}

// ─── Derived insight helpers ────────────────────────────────────────────────

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
    direction,
  }
}

/**
 * Builds the plain-English "Today's Signal" verdict using the new correlation shape.
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

  const primary = getPrimary(correlation)
  const strength = primary?.strength ?? null
  const isMomentum = primary?.direction?.includes("momentum") ?? null
  const corrValue = primary?.correlation ?? null
  const beatsMomentum = correlation?.baseline?.primary_beats_momentum ?? null
  const sampleSize = correlation?.sample_size ?? null

  // Build the narrative sentence
  let narrative = `${ticker} sentiment is currently ${sentimentLabel} (${score > 0 ? "+" : ""}${score}).`

  if (strength === "strong" || strength === "weak") {
    const followVerb = isMomentum ? "tends to follow" : "historically moves opposite to"
    const strengthWord = strength === "strong" ? "a strong" : "a weak"
    narrative += ` Historically, next-day price ${followVerb} sentiment shifts with ${strengthWord} ${isMomentum ? "momentum" : "contrarian"} relationship`
    if (corrValue !== null) narrative += ` (r=${corrValue}).`
    else narrative += `.`
    if (beatsMomentum === false) {
      narrative += ` Note: this signal does not outperform a simple momentum baseline.`
    }
  } else if (strength === "inconclusive") {
    narrative += ` Historical data is inconclusive — no statistically significant link between sentiment shifts and next-day price.`
  }

  if (trend?.direction !== "flat" && trend?.delta !== null) {
    const trendWord = trend.direction === "up" ? "improving" : "deteriorating"
    narrative += ` Sentiment has been ${trendWord} over the past week (${trend.delta > 0 ? "+" : ""}${trend.delta}).`
  }

  return {
    direction,
    sentimentLabel,
    score,
    strength,
    isMomentum,
    correlation: corrValue,
    beatsMomentum,
    sampleSize,
    narrative,
  }
}

// ─── Divergence Signal Card ───────────────────────────────────────────────

function DivergenceCard({ data, loading }) {
  if (loading) {
    return (
      <div className="panel" style={{ borderLeft: "3px solid var(--border2)" }}>
        <div className="panel-header">
          <span className="panel-title">DIVERGENCE SIGNAL</span>
          <span className="panel-title" style={{ color: "var(--muted)" }}>7D WINDOW</span>
        </div>
        <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div className="skeleton" style={{ height: "28px", width: "180px", borderRadius: "2px" }} />
          <div style={{ display: "flex", gap: "24px" }}>
            <div className="skeleton" style={{ height: "48px", width: "80px", borderRadius: "2px" }} />
            <div className="skeleton" style={{ height: "48px", width: "80px", borderRadius: "2px" }} />
          </div>
          <div className="skeleton" style={{ height: "40px", width: "100%", borderRadius: "2px" }} />
        </div>
      </div>
    )
  }

  if (!data || data.message) {
    return (
      <div className="panel" style={{ borderLeft: "3px solid var(--border2)" }}>
        <div className="panel-header">
          <span className="panel-title">DIVERGENCE SIGNAL</span>
          <span className="panel-title" style={{ color: "var(--muted)" }}>7D WINDOW</span>
        </div>
        <div className="panel-body" style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>
          {data?.message ?? "Not enough data yet."}
        </div>
      </div>
    )
  }

  const { divergence, sentiment_direction, price_direction, sentiment_change_7d, price_change_7d, streak_days, summary } = data

  const borderColor = divergence === "bullish" ? "var(--positive)" : divergence === "bearish" ? "var(--negative)" : "var(--border2)"
  const badgeColor = divergence === "bullish" ? "var(--positive)" : divergence === "bearish" ? "var(--negative)" : "var(--muted)"
  const badgeBg = divergence === "bullish" ? "rgba(63,185,80,0.08)" : divergence === "bearish" ? "rgba(248,81,73,0.08)" : "rgba(139,148,158,0.08)"
  const badgeBorder = divergence === "bullish" ? "rgba(63,185,80,0.3)" : divergence === "bearish" ? "rgba(248,81,73,0.3)" : "rgba(139,148,158,0.3)"
  const label = divergence === "bullish" ? "BULLISH DIVERGENCE" : divergence === "bearish" ? "BEARISH DIVERGENCE" : "ALIGNED"

  const dirIcon = d => d === "up" ? "↑" : d === "down" ? "↓" : "→"
  const dirLabel = d => d === "up" ? "rising" : d === "down" ? "falling" : "stable"
  const sentColor = sentiment_change_7d > 0 ? "var(--positive)" : sentiment_change_7d < 0 ? "var(--negative)" : "var(--muted)"
  const priceColor = price_change_7d > 0 ? "var(--positive)" : price_change_7d < 0 ? "var(--negative)" : "var(--muted)"

  return (
    <div className="panel" style={{ borderLeft: `3px solid ${borderColor}` }}>
      <div className="panel-header">
        <span className="panel-title">DIVERGENCE SIGNAL</span>
        <span className="panel-title" style={{ color: "var(--muted)" }}>7D WINDOW</span>
      </div>
      <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        <span style={{
          display: "inline-block", alignSelf: "flex-start",
          fontFamily: "var(--mono)", fontSize: "11px", fontWeight: 700,
          letterSpacing: "0.12em", padding: "5px 14px", borderRadius: "2px",
          background: badgeBg, color: badgeColor, border: `1px solid ${badgeBorder}`,
        }}>
          {label}
        </span>

        <div style={{ display: "flex", gap: "24px", flexWrap: "wrap", alignItems: "flex-start" }}>
          <div>
            <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
              Sentiment 7D
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600, color: sentColor, lineHeight: 1 }}>
              {sentiment_change_7d > 0 ? "+" : ""}{sentiment_change_7d}
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "3px" }}>
              {dirIcon(sentiment_direction)} {dirLabel(sentiment_direction)}
            </div>
          </div>

          <div style={{ width: "1px", background: "var(--border)", alignSelf: "stretch" }} />

          <div>
            <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
              Price 7D
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600, color: priceColor, lineHeight: 1 }}>
              {price_change_7d > 0 ? "+" : ""}{price_change_7d}%
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "3px" }}>
              {dirIcon(price_direction)} {dirLabel(price_direction)}
            </div>
          </div>

          {streak_days > 0 && (
            <>
              <div style={{ width: "1px", background: "var(--border)", alignSelf: "stretch" }} />
              <div>
                <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
                  Duration
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600, color: "var(--accent)", lineHeight: 1 }}>
                  {streak_days}d
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "3px" }}>
                  consecutive
                </div>
              </div>
            </>
          )}
        </div>

        <p style={{ fontFamily: "var(--sans)", fontSize: "13px", lineHeight: "1.65", color: "var(--text)", margin: 0 }}>
          {summary}
        </p>

        {divergence !== "none" && (
          <div style={{
            padding: "10px 14px", background: "var(--surface2)",
            border: "1px solid var(--border)", borderRadius: "2px",
            fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)", lineHeight: "1.6",
          }}>
            💡 {divergence === "bullish"
              ? "Bullish divergences suggest improving narrative has not yet been priced in — historically may precede upward corrections in momentum markets."
              : "Bearish divergences suggest the market is pricing in optimism not supported by news flow — watch for potential downside realignment."}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Info tooltip ──────────────────────────────────────────────────────────

function InfoTip({ text }) {
  const [visible, setVisible] = useState(false)
  return (
    <span
      style={{ position: "relative", display: "inline-flex", alignItems: "center", marginLeft: "4px" }}
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      <span style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: "12px", height: "12px", borderRadius: "50%",
        border: "1px solid var(--border2)", color: "var(--muted)",
        fontFamily: "var(--mono)", fontSize: "8px", cursor: "help",
        lineHeight: 1, userSelect: "none", flexShrink: 0,
      }}>?</span>
      {visible && (
        <span style={{
          position: "absolute", bottom: "calc(100% + 8px)", left: "50%",
          transform: "translateX(-50%)",
          background: "var(--surface2)", border: "1px solid var(--border)",
          borderRadius: "4px", padding: "10px 12px",
          fontSize: "12px", fontFamily: "var(--sans)", color: "var(--text)",
          lineHeight: "1.55", width: "230px",
          zIndex: 300, pointerEvents: "none",
          boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
        }}>
          {text}
        </span>
      )}
    </span>
  )
}

// ─── Strength meter bar ────────────────────────────────────────────────────

function StrengthMeter({ strength }) {
  const levels = ["inconclusive", "weak", "strong"]
  const idx = levels.indexOf(strength)
  const colors = ["#8b949e", "#f0b429", "#3fb950"]
  const safeIdx = idx >= 0 ? idx : 0
  return (
    <div style={{ display: "flex", gap: "3px", alignItems: "center" }}>
      {levels.map((l, i) => (
        <div
          key={l}
          style={{
            width: "28px", height: "4px", borderRadius: "2px",
            background: i <= safeIdx ? colors[safeIdx] : "var(--border2)",
            transition: "background 0.3s",
          }}
        />
      ))}
      <span style={{
        fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em",
        color: colors[safeIdx], textTransform: "uppercase", marginLeft: "4px"
      }}>
        {strength}
      </span>
    </div>
  )
}

// ─── Trend Arrow ───────────────────────────────────────────────────────────

function TrendArrow({ trend }) {
  if (!trend) return <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)" }}>—</span>

  const { direction, delta } = trend

  const arrowMap = { up: "↑", down: "↓", flat: "→" }
  const colorMap = { up: "var(--positive)", down: "var(--negative)", flat: "var(--neutral)" }
  const labelMap = { up: "improving", down: "worsening", flat: "stable" }

  const deltaDisplay = delta !== null
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
          {signal.isMomentum !== null && signal.strength !== "inconclusive" && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
              <span style={{
                fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.08em",
                padding: "2px 8px", borderRadius: "2px",
                background: "var(--surface2)", color: "var(--muted)",
                border: "1px solid var(--border2)",
              }}>
                {signal.isMomentum ? "MOMENTUM" : "CONTRARIAN"}
              </span>
              <InfoTip text={signal.isMomentum
                ? "Positive sentiment shifts have historically preceded price rises for this ticker."
                : "Positive sentiment shifts have historically preceded price drops — the market may have already priced the news in."} />
            </span>
          )}
          {signal.beatsMomentum === true && signal.strength !== "inconclusive" && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
              <span style={{
                fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.08em",
                padding: "2px 8px", borderRadius: "2px",
                background: "rgba(63,185,80,0.1)", color: "var(--positive)",
                border: "1px solid rgba(63,185,80,0.3)",
              }}>
                BEATS BASELINE
              </span>
              <InfoTip text="The sentiment signal has historically outperformed a simple price momentum strategy over the last 180 days." />
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

      {/* Bottom row: score + trend + correlation */}
      <div style={{ display: "flex", gap: "24px", flexWrap: "wrap", alignItems: "flex-start", borderTop: "1px solid var(--border)", paddingTop: "14px" }}>
        <div>
          <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px", display: "flex", alignItems: "center" }}>
            Sentiment Score
            <InfoTip text="Average FinBERT score across today's headlines. Ranges from -1 (very negative) to +1 (very positive). Scored using a financial-domain AI model." />
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
          <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px", display: "flex", alignItems: "center" }}>
            7-Day Trend
            <InfoTip text="Average sentiment over the last 7 days vs the 7 days before that. Shows whether the overall news tone is improving or deteriorating." />
          </div>
          <TrendArrow trend={trend} />
        </div>

        {signal.correlation !== null && (
          <>
            <div style={{ width: "1px", background: "var(--border)", alignSelf: "stretch" }} />
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px", display: "flex", alignItems: "center" }}>
                Correlation (r)
                <InfoTip text="Pearson correlation between daily sentiment shifts and next-day price returns over the last 180 days. +1 = perfect positive link, -1 = perfect inverse link, 0 = no relationship." />
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600, color: "var(--accent2)", lineHeight: 1 }}>
                {signal.correlation > 0 ? "+" : ""}{signal.correlation}
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "3px" }}>
                next-day return · n={signal.sampleSize ?? "?"}
              </div>
            </div>
          </>
        )}

        {signal.shiftPercentile !== undefined && signal.shiftPercentile !== null && (
          <>
            <div style={{ width: "1px", background: "var(--border)", alignSelf: "stretch" }} />
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px", display: "flex", alignItems: "center" }}>
                Shift Percentile
                <InfoTip text="How large today's sentiment shift is compared to all historical daily shifts. 90th percentile = larger move than 90% of recorded days. Higher = rarer, potentially more significant." />
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 600, color: signal.shiftPercentile >= 75 ? "var(--accent)" : "var(--muted)", lineHeight: 1 }}>
                {signal.shiftPercentile}th
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "3px" }}>
                {signal.shiftMagnitude} · {signal.articleCount} articles
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

  .category-bar {
    display: flex;
    gap: 4px;
    padding: 10px 24px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    scrollbar-width: none;
  }

  .category-bar::-webkit-scrollbar { display: none; }

  .category-btn {
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 6px 16px;
    border: 1px solid var(--border);
    border-radius: 16px;
    cursor: pointer;
    background: var(--surface);
    color: var(--muted);
    transition: all 0.15s;
    white-space: nowrap;
  }

  .category-btn:hover {
    color: var(--text);
    border-color: var(--border2);
  }

  .category-btn.active {
    color: var(--bg);
    background: var(--accent);
    border-color: var(--accent);
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

  .stat-block {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 10px 14px;
    margin-bottom: 10px;
  }

  .stat-block-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 4px 0;
  }

  .stat-block-key {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    color: var(--muted);
    text-transform: uppercase;
  }

  .stat-block-val {
    font-family: var(--mono);
    font-size: 12px;
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

  .api-key-display {
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 2px;
    padding: 10px 14px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--accent);
    letter-spacing: 0.04em;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
    word-break: break-all;
    gap: 12px;
  }

  .api-key-masked {
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .api-usage-bar {
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    margin: 8px 0 4px;
  }

  .api-usage-bar-fill {
    height: 100%;
    background: var(--accent2);
    border-radius: 2px;
    transition: width 0.3s;
  }

  .api-copy-btn {
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.1em;
    padding: 3px 8px;
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 2px;
    color: var(--muted);
    cursor: pointer;
    text-transform: uppercase;
    flex-shrink: 0;
    transition: all 0.15s;
  }

  .api-copy-btn:hover { color: var(--accent); border-color: var(--accent); }

  .api-warn-box {
    background: rgba(240,180,41,0.05);
    border: 1px solid rgba(240,180,41,0.25);
    border-radius: 2px;
    padding: 10px 12px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--accent);
    letter-spacing: 0.04em;
    margin-bottom: 12px;
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
      {[...Array(4)].map((_, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", paddingBottom: "8px", borderBottom: "1px solid var(--border)" }}>
          <div className="skeleton" style={{ height: "10px", width: "100px", borderRadius: "2px" }} />
          <div className="skeleton" style={{ height: "10px", width: "60px", borderRadius: "2px" }} />
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

// ─── Headline Impact Panel ────────────────────────────────────────────────

function HeadlineImpactPanel({ ticker }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [days, setDays] = useState(90)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setData(null)
    axios.get(`${API}/headline-impact/${ticker}?days=${days}&limit=20`)
      .then(r => { if (!cancelled) { setData(r.data); setLoading(false) } })
      .catch(() => { if (!cancelled) { setData({ error: true }); setLoading(false) } })
    return () => { cancelled = true }
  }, [ticker, days])

  const daysBtn = (d) => ({
    fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
    padding: "4px 10px", borderRadius: "2px", cursor: "pointer", transition: "all 0.15s",
    border: `1px solid ${days === d ? "var(--accent)" : "var(--border)"}`,
    background: days === d ? "rgba(240,180,41,0.08)" : "transparent",
    color: days === d ? "var(--accent)" : "var(--muted)",
  })

  const headlines = data?.headlines ?? []
  const summary = data?.summary

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">HEADLINE IMPACT</span>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {summary && (
            <span className="panel-title" style={{ color: "var(--muted)" }}>
              {summary.confirmed_pct}% CONFIRMED · {100 - summary.confirmed_pct}% CONTRARIAN
            </span>
          )}
          {[30, 90, 180].map(d => (
            <button key={d} style={daysBtn(d)} onClick={() => setDays(d)}>{d}D</button>
          ))}
        </div>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>

        {loading && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1px", background: "var(--border)" }}>
            {[...Array(6)].map((_, i) => (
              <div key={i} style={{ background: "var(--surface)", padding: "12px 16px", display: "flex", gap: "12px" }}>
                <div className="skeleton" style={{ width: "60px", height: "18px", borderRadius: "2px", flexShrink: 0 }} />
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "5px" }}>
                  <div className="skeleton" style={{ width: "100%", height: "12px", borderRadius: "2px" }} />
                  <div className="skeleton" style={{ width: "60%", height: "10px", borderRadius: "2px" }} />
                </div>
                <div className="skeleton" style={{ width: "50px", height: "18px", borderRadius: "2px", flexShrink: 0 }} />
              </div>
            ))}
          </div>
        )}

        {!loading && data?.error && (
          <div style={{ padding: "16px", fontFamily: "var(--mono)", fontSize: "11px", color: "var(--negative)" }}>
            Failed to load headline impact data.
          </div>
        )}

        {!loading && !data?.error && headlines.length === 0 && (
          <div style={{ padding: "16px", fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>
            Not enough data — need headlines with both strong sentiment and significant next-day price moves.
          </div>
        )}

        {!loading && headlines.length > 0 && (
          <>
            {/* Column header */}
            <div style={{
              display: "grid",
              gridTemplateColumns: "90px 1fr 88px 72px 96px",
              fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.08em",
              color: "var(--muted)", textTransform: "uppercase",
              padding: "7px 16px", background: "var(--surface2)",
              borderBottom: "1px solid var(--border)",
            }}>
              <span>DATE</span>
              <span>HEADLINE</span>
              <span>SENTIMENT</span>
              <span>NEXT-DAY Δ</span>
              <span style={{ textAlign: "right" }}>SIGNAL</span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "1px", background: "var(--border)" }}>
              {headlines.map((h, i) => {
                const sentColor = h.sentiment_score > 0.3 ? "var(--positive)"
                  : h.sentiment_score > 0 ? "rgba(63,185,80,0.7)"
                  : h.sentiment_score < -0.3 ? "var(--negative)"
                  : "rgba(248,81,73,0.7)"
                const priceColor = h.next_day_return_pct > 0 ? "var(--positive)" : "var(--negative)"
                const priceArrow = h.next_day_return_pct > 0 ? "↑" : "↓"
                const alignColor = h.alignment === "confirmed" ? "var(--positive)" : "var(--accent)"
                const alignBg = h.alignment === "confirmed" ? "rgba(63,185,80,0.08)" : "rgba(240,180,41,0.08)"
                const alignBorder = h.alignment === "confirmed" ? "rgba(63,185,80,0.25)" : "rgba(240,180,41,0.25)"

                return (
                  <div key={h.id ?? i} style={{
                    display: "grid",
                    gridTemplateColumns: "90px 1fr 88px 72px 96px",
                    padding: "10px 16px",
                    background: "var(--surface)",
                    alignItems: "start",
                    gap: "8px",
                    transition: "background 0.1s",
                  }}
                    onMouseOver={e => e.currentTarget.style.background = "var(--surface2)"}
                    onMouseOut={e => e.currentTarget.style.background = "var(--surface)"}
                  >
                    <div style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", lineHeight: 1.5 }}>
                      <div>{new Date(h.published_at + "Z").toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</div>
                      <div style={{ fontSize: "9px", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h.source}</div>
                    </div>

                    <a
                      href={h.url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ fontFamily: "var(--sans)", fontSize: "12px", color: "var(--text)", lineHeight: 1.5, textDecoration: "none" }}
                      onMouseOver={e => e.currentTarget.style.color = "var(--accent2)"}
                      onMouseOut={e => e.currentTarget.style.color = "var(--text)"}
                    >
                      {h.title}
                    </a>

                    <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                      <span className={`sentiment-pill pill-${h.sentiment_label}`} style={{ marginTop: 0 }}>
                        {h.sentiment_label.slice(0, 3).toUpperCase()}
                      </span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: sentColor, fontWeight: 600 }}>
                        {h.sentiment_score > 0 ? "+" : ""}{h.sentiment_score}
                      </span>
                    </div>

                    <span style={{ fontFamily: "var(--mono)", fontSize: "11px", color: priceColor, fontWeight: 600 }}>
                      {priceArrow} {Math.abs(h.next_day_return_pct)}%
                    </span>

                    <div style={{ textAlign: "right" }}>
                      <span style={{
                        fontFamily: "var(--mono)", fontSize: "9px", fontWeight: 700,
                        letterSpacing: "0.1em", padding: "2px 8px", borderRadius: "2px",
                        background: alignBg, color: alignColor, border: `1px solid ${alignBorder}`,
                      }}>
                        {h.alignment === "confirmed" ? "CONFIRMED" : "CONTRARIAN"}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>

            <div style={{
              padding: "10px 16px", background: "var(--surface2)",
              borderTop: "1px solid var(--border)",
              fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", lineHeight: 1.6,
            }}>
              Ranked by impact score (|sentiment| × |next-day return|). <strong style={{ color: "var(--positive)" }}>Confirmed</strong> = sentiment direction matched next-day price. <strong style={{ color: "var(--accent)" }}>Contrarian</strong> = market moved opposite to sentiment.
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ─── Backtest Panel ────────────────────────────────────────────────────────

const BT_DATE_FMT = (s) => {
  if (!s) return ""
  const d = new Date(s + "T00:00:00")
  return d.toLocaleDateString("en-GB", { month: "short", day: "numeric" })
}

function BacktestPanel({ ticker }) {
  const [btData, setBtData] = useState(null)
  const [btLoading, setBtLoading] = useState(false)
  const [btSignal, setBtSignal] = useState("divergence")
  const [btHoldDays, setBtHoldDays] = useState(7)

  useEffect(() => {
    let cancelled = false
    setBtLoading(true)
    setBtData(null)
    axios.get(`${API}/backtest/${ticker}?signal=${btSignal}&hold_days=${btHoldDays}`)
      .then(r => { if (!cancelled) { setBtData(r.data); setBtLoading(false) } })
      .catch(() => { if (!cancelled) { setBtData({ error: true }); setBtLoading(false) } })
    return () => { cancelled = true }
  }, [ticker, btSignal, btHoldDays])

  const ctrlBtn = (active) => ({
    fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
    padding: "4px 10px", borderRadius: "2px", cursor: "pointer", transition: "all 0.15s",
    border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
    background: active ? "rgba(240,180,41,0.08)" : "transparent",
    color: active ? "var(--accent)" : "var(--muted)",
    textTransform: "uppercase",
  })

  const summary = btData?.summary
  const trades = btData?.trades ?? []
  const equityCurve = btData?.equity_curve ?? []

  const retColor = (v) => v > 0 ? "var(--positive)" : v < 0 ? "var(--negative)" : "var(--muted)"

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">BACKTEST SIMULATOR</span>
        {!btLoading && summary && (
          <span className="panel-title" style={{ color: "var(--muted)" }}>
            {btData.window_days}D WINDOW · {summary.total_trades} TRADES
          </span>
        )}
      </div>
      <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>

        {/* Controls */}
        <div style={{ display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", letterSpacing: "0.08em" }}>SIGNAL</span>
            {["divergence", "shift"].map(s => (
              <button key={s} style={ctrlBtn(btSignal === s)} onClick={() => setBtSignal(s)}>
                {s === "divergence" ? "DIVERGENCE" : "SHIFT"}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", letterSpacing: "0.08em" }}>HOLD</span>
            {[1, 3, 7, 14].map(h => (
              <button key={h} style={ctrlBtn(btHoldDays === h)} onClick={() => setBtHoldDays(h)}>{h}D</button>
            ))}
          </div>
        </div>

        {btLoading && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: "12px" }}>
              {[...Array(5)].map((_, i) => <div key={i} className="skeleton" style={{ height: "64px", borderRadius: "4px" }} />)}
            </div>
            <div className="skeleton" style={{ height: "200px", borderRadius: "4px" }} />
          </div>
        )}

        {!btLoading && btData?.message && (
          <div style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>
            {btData.message}
          </div>
        )}

        {!btLoading && btData?.error && (
          <div style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--negative)" }}>
            Failed to load backtest data.
          </div>
        )}

        {!btLoading && summary && (
          <>
            {/* Summary stats */}
            <div className="stat-row">
              <div className="stat-card">
                <div className="stat-label">Total Return</div>
                <div className="stat-value" style={{ color: retColor(summary.total_return_pct) }}>
                  {summary.total_return_pct > 0 ? "+" : ""}{summary.total_return_pct}%
                </div>
                <div className="stat-sub">compounded</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Win Rate</div>
                <div className="stat-value" style={{ color: summary.win_rate >= 0.5 ? "var(--positive)" : "var(--negative)" }}>
                  {(summary.win_rate * 100).toFixed(0)}%
                </div>
                <div className="stat-sub">{summary.winning_trades}/{summary.total_trades} trades</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Avg / Trade</div>
                <div className="stat-value" style={{ color: retColor(summary.avg_return_pct) }}>
                  {summary.avg_return_pct > 0 ? "+" : ""}{summary.avg_return_pct}%
                </div>
                <div className="stat-sub">max drawdown {summary.max_drawdown_pct}%</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">vs Buy &amp; Hold</div>
                <div className="stat-value" style={{ color: retColor(summary.alpha_pct) }}>
                  {summary.alpha_pct > 0 ? "+" : ""}{summary.alpha_pct}%
                </div>
                <div className="stat-sub">BH: {summary.buy_hold_return_pct > 0 ? "+" : ""}{summary.buy_hold_return_pct}%</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Sharpe</div>
                <div className="stat-value" style={{ color: summary.sharpe >= 1 ? "var(--positive)" : summary.sharpe >= 0 ? "var(--accent)" : "var(--negative)" }}>
                  {summary.sharpe ?? "—"}
                </div>
                <div className="stat-sub">annualised</div>
              </div>
            </div>

            {/* Equity curve */}
            {equityCurve.length > 1 && (
              <div style={{ marginTop: "4px" }}>
                <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "8px" }}>
                  EQUITY CURVE
                </div>
                <ResponsiveContainer width="100%" height={200}>
                  <ComposedChart data={equityCurve} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                    <CartesianGrid strokeDasharray="2 4" stroke="#21262d" vertical={false} />
                    <XAxis
                      dataKey="date"
                      tickFormatter={BT_DATE_FMT}
                      stroke="#30363d"
                      tick={{ fill: "#7d8590", fontSize: 9, fontFamily: "IBM Plex Mono" }}
                      tickLine={false}
                      axisLine={{ stroke: "#21262d" }}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      stroke="#30363d"
                      tick={{ fill: "#7d8590", fontSize: 9, fontFamily: "IBM Plex Mono" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={v => `${v}`}
                      width={36}
                    />
                    <Tooltip
                      content={({ active, payload, label }) => {
                        if (!active || !payload?.length) return null
                        return (
                          <div className="custom-tooltip">
                            <div className="tooltip-label">{BT_DATE_FMT(label)}</div>
                            {payload.map((p, i) => (
                              <div className="tooltip-row" key={i}>
                                <span className="tooltip-key">{p.name}</span>
                                <span className="tooltip-val" style={{ color: p.color }}>{p.value?.toFixed(1)}</span>
                              </div>
                            ))}
                          </div>
                        )
                      }}
                    />
                    <ReferenceLine y={100} stroke="#30363d" strokeDasharray="4 2" />
                    <Line dataKey="portfolio" name="Strategy" stroke="var(--accent)" dot={false} strokeWidth={1.5} connectNulls />
                    <Line dataKey="buy_hold" name="Buy & Hold" stroke="var(--accent2)" dot={false} strokeWidth={1} strokeDasharray="4 2" connectNulls />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Trades table */}
            {trades.length > 0 && (
              <div>
                <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "8px" }}>
                  TRADE LOG
                </div>
                <div style={{ overflowX: "auto" }}>
                  <div style={{ minWidth: "480px" }}>
                    <div style={{
                      display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr 80px",
                      fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.08em",
                      color: "var(--muted)", textTransform: "uppercase",
                      padding: "6px 12px", background: "var(--surface2)",
                      borderBottom: "1px solid var(--border)",
                    }}>
                      <span>ENTRY</span><span>EXIT</span><span>IN (£)</span><span>OUT (£)</span><span style={{ textAlign: "right" }}>RETURN</span>
                    </div>
                    <div style={{ maxHeight: "200px", overflowY: "auto", background: "var(--border)", display: "flex", flexDirection: "column", gap: "1px" }}>
                      {trades.map((t, i) => (
                        <div key={i} style={{
                          display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr 80px",
                          fontFamily: "var(--mono)", fontSize: "11px",
                          padding: "8px 12px", background: "var(--surface)",
                          alignItems: "center",
                        }}>
                          <span style={{ color: "var(--muted)" }}>{BT_DATE_FMT(t.entry_date)}</span>
                          <span style={{ color: "var(--muted)" }}>{BT_DATE_FMT(t.exit_date)}</span>
                          <span style={{ color: "var(--text)" }}>{t.entry_price.toLocaleString()}</span>
                          <span style={{ color: "var(--text)" }}>{t.exit_price.toLocaleString()}</span>
                          <span style={{ color: retColor(t.return_pct), textAlign: "right", fontWeight: 600 }}>
                            {t.return_pct > 0 ? "+" : ""}{t.return_pct}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div className="disclaimer">
              ⚠ Long-only strategy. Entry at next close after signal, exit after {btHoldDays} calendar days. Past results do not predict future performance. Not financial advice.
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ─── Leaderboard (public SEO page, no auth) ──────────────────────────────────
// Lives at /leaderboard.  Pulls GET /leaderboard once on mount; the backend
// caches via Cache-Control so the CDN absorbs the load.  Default sort is the
// backend's: biggest absolute 24h sentiment movers first.  Each row links to
// the existing per-ticker SEO landing page on sentimentfx.org for SEO juice.

const CATEGORY_FILTERS = [
  { key: "all",         label: "All" },
  { key: "crypto",      label: "Crypto" },
  { key: "fx",          label: "FX" },
  { key: "stocks",      label: "Stocks" },
  { key: "etfs",        label: "ETFs" },
  { key: "commodities", label: "Commodities" },
]

const LEADERBOARD_COLS = [
  // key on returned row, label, sort accessor (numeric, NaN = last)
  { key: "ticker",                label: "Asset",        align: "left",  sort: r => r.ticker },
  { key: "sentiment_24h",         label: "Sentiment",    align: "right", sort: r => r.sentiment_24h ?? -Infinity },
  { key: "sentiment_change_24h",  label: "Δ 24h",        align: "right", sort: r => r.sentiment_change_24h ?? -Infinity, abs: true },
  { key: "article_count_24h",     label: "Articles",     align: "right", sort: r => r.article_count_24h ?? 0 },
  { key: "price",                 label: "Price",        align: "right", sort: r => r.price ?? -Infinity },
  { key: "price_change_24h_pct",  label: "24h %",        align: "right", sort: r => r.price_change_24h_pct ?? -Infinity },
]

function _displayLabel(t) {
  return FX_LABELS[t] ?? COMMODITY_LABELS[t] ?? t
}

function _formatPrice(v, ticker) {
  if (v === null || v === undefined) return "—"
  if (FX_TICKERS.includes(ticker)) return v.toFixed(4)
  if (v >= 1000) return `£${(v / 1000).toFixed(2)}k`
  if (v >= 1)    return `£${v.toFixed(2)}`
  return `£${v.toFixed(4)}`
}

function _signedPct(v) {
  if (v === null || v === undefined) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(2)}%`
}

function _signedScore(v) {
  if (v === null || v === undefined) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(3)}`
}

function _scoreColor(v) {
  if (v === null || v === undefined) return "var(--muted)"
  if (v > 0.05) return "var(--positive)"
  if (v < -0.05) return "var(--negative)"
  return "var(--neutral)"
}

// Admin-only backtest leaderboard.  Visually distinct from the public table
// (red accent border + "ADMIN" eyebrow) so it's obvious this isn't public data.
// New backend shape: each row has {full, in_sample, out_of_sample} blocks,
// each with {gross, net} sub-blocks.  The OOS-net column is the most honest
// single number — it's the strategy's edge on the window the thresholds
// never saw, net of round-trip transaction costs.
const _pct = (v, opts = {}) => {
  if (v == null) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(opts.dp ?? 2)}%`
}
const _winRate = (v) => v == null ? "—" : `${Math.round(v * 100)}%`
const _g = (path) => (r) => path.split(".").reduce((o, k) => o?.[k], r)

const ADMIN_BOARD_COLS = [
  { key: "ticker",     label: "Asset",      align: "left",  hint: "Ticker / display label",
    val: (r) => FX_LABELS[r.ticker] ?? COMMODITY_LABELS[r.ticker] ?? r.ticker },
  { key: "category",   label: "Cat",        align: "left",  hint: "Asset category drives default cost bps",
    val: (r) => (r.category ?? "").toUpperCase() },
  { key: "full_trades",        label: "Trades",     align: "right", hint: "Total trades in the full 180d window",
    val: (r) => _g("full.gross.trades")(r) ?? 0,
    sort: (r) => _g("full.gross.trades")(r) },
  { key: "full_win_rate",      label: "WR",         align: "right", hint: "Win rate across the full window (gross)",
    val: (r) => _winRate(_g("full.gross.win_rate")(r)),
    sort: (r) => _g("full.gross.win_rate")(r) },
  { key: "full_gross_total",   label: "Full Gross", align: "right", hint: "Compounded total return over 180d, before costs",
    val: (r) => _pct(_g("full.gross.total_return_pct")(r)),
    sort: (r) => _g("full.gross.total_return_pct")(r),
    color: (r) => _g("full.gross.total_return_pct")(r) },
  { key: "full_net_total",     label: "Full Net",   align: "right", hint: "Same window, AFTER round-trip transaction costs",
    val: (r) => _pct(_g("full.net.total_return_pct")(r)),
    sort: (r) => _g("full.net.total_return_pct")(r),
    color: (r) => _g("full.net.total_return_pct")(r) },
  { key: "oos_trades",         label: "OOS n",      align: "right", hint: "Trades in the out-of-sample window (last 1/3)",
    val: (r) => _g("out_of_sample.gross.trades")(r) ?? 0,
    sort: (r) => _g("out_of_sample.gross.trades")(r) },
  { key: "oos_win_rate",       label: "OOS WR",     align: "right", hint: "Win rate in out-of-sample window",
    val: (r) => _winRate(_g("out_of_sample.gross.win_rate")(r)),
    sort: (r) => _g("out_of_sample.gross.win_rate")(r) },
  { key: "oos_gross_total",    label: "OOS Gross",  align: "right", hint: "OOS total return, before costs",
    val: (r) => _pct(_g("out_of_sample.gross.total_return_pct")(r)),
    sort: (r) => _g("out_of_sample.gross.total_return_pct")(r),
    color: (r) => _g("out_of_sample.gross.total_return_pct")(r) },
  { key: "oos_net_total",      label: "OOS Net",    align: "right", hint: "THE honest number: OOS return after costs. If positive across the board, edge probably real. If broadly negative, edge is mirage.",
    val: (r) => _pct(_g("out_of_sample.net.total_return_pct")(r)),
    sort: (r) => _g("out_of_sample.net.total_return_pct")(r),
    color: (r) => _g("out_of_sample.net.total_return_pct")(r),
    bold: true },
  { key: "is_oos_gap",         label: "IS→OOS Δ",   align: "right", hint: "In-sample net minus out-of-sample net. Large positive = overfit; small or negative = robust.",
    val: (r) => {
      const is = _g("in_sample.net.total_return_pct")(r)
      const oos = _g("out_of_sample.net.total_return_pct")(r)
      if (is == null || oos == null) return "—"
      const delta = is - oos
      return `${delta > 0 ? "+" : ""}${delta.toFixed(2)}%`
    },
    sort: (r) => {
      const is = _g("in_sample.net.total_return_pct")(r)
      const oos = _g("out_of_sample.net.total_return_pct")(r)
      if (is == null || oos == null) return null
      return is - oos
    },
    color: (r) => {
      const is = _g("in_sample.net.total_return_pct")(r)
      const oos = _g("out_of_sample.net.total_return_pct")(r)
      if (is == null || oos == null) return null
      // Inverted: a LARGE positive gap is BAD (overfit), so colour negative.
      return -(is - oos)
    } },
  { key: "costs_pct_per_trade", label: "Cost/trade", align: "right", hint: "Round-trip transaction cost assumed for this ticker (per trade)",
    val: (r) => r.costs_pct_per_trade == null ? "—" : `${r.costs_pct_per_trade.toFixed(2)}%`,
    sort: (r) => r.costs_pct_per_trade },
]

// Pill-toggle helper used by the admin control bar.  Tiny, intentionally
// dumb — no portals, no popovers, just monospace text in a 1-px box that
// flips style based on whether it's the active value.
function _AdminPill({ active, onClick, children, title }) {
  return (
    <button onClick={onClick} title={title} style={{
      fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
      padding: "5px 10px",
      border: active ? "1px solid var(--accent2)" : "1px solid var(--border2)",
      borderRadius: "2px",
      background: active ? "rgba(88,166,255,0.08)" : "transparent",
      color: active ? "var(--accent2)" : "var(--muted)",
      cursor: "pointer", textTransform: "uppercase",
    }}>{children}</button>
  )
}

function AdminBacktestBoard({ board, sortKey, onSort, params, onParamsChange, loading }) {
  const cols = ADMIN_BOARD_COLS
  const activeCol = cols.find(c => c.key === sortKey)
  const sorted = [...(board?.rows ?? [])].sort((a, b) => {
    if (!activeCol?.sort) return 0
    const av = activeCol.sort(a), bv = activeCol.sort(b)
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    if (typeof av === "string") return av.localeCompare(bv)
    return bv - av
  })

  const generated = board?.computed_at ? new Date(board.computed_at) : null

  // Headline aggregate across all OOS-net values — "does the strategy work in
  // aggregate" is more useful than any single ticker.  Average across rows
  // that have a settled OOS net.
  const oosNets = (board?.rows ?? [])
    .map(r => _g("out_of_sample.net.total_return_pct")(r))
    .filter(v => v != null)
  const avgOosNet = oosNets.length
    ? oosNets.reduce((a, b) => a + b, 0) / oosNets.length
    : null
  const positiveCount = oosNets.filter(v => v > 0).length

  return (
    <section style={{
      marginTop: "48px", padding: "24px",
      border: "1px solid var(--negative)", borderRadius: "2px",
      background: "var(--surface)",
    }}>
      <div style={{
        fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.2em",
        color: "var(--negative)", textTransform: "uppercase", marginBottom: "8px",
      }}>● Admin · is the edge real?</div>
      <h2 style={{
        fontFamily: "var(--mono)", fontSize: "18px", fontWeight: 500,
        color: "var(--text)", marginBottom: "6px",
      }}>Backtest with out-of-sample split + transaction costs</h2>
      <p style={{
        fontFamily: "var(--sans)", fontSize: "12px", color: "var(--muted)",
        marginBottom: "16px", maxWidth: "720px",
      }}>
        {board?.signal === "shift" ? "Sentiment-shift" : "Divergence"} signal,
        {" "}{board?.hold_days}d hold,
        {" "}{board?.direction_mode === "contrarian" ? "long-on-bearish (contrarian)" : "long-on-bullish (momentum)"}.
        {" "}Split: {board?.split_ratio ?? "2/3 IS · 1/3 OOS"}.
        Default cost assumptions: 30 bps crypto · 15 bps FX · 6 bps stocks · 4 bps ETFs · 12 bps commodities (per round-trip).
        {" "}<strong style={{ color: "var(--text)" }}>Read OOS Net first.</strong>
        {" "}If it's broadly positive across tickers, edge probably survives a regime change AND transaction costs.
        If broadly negative or near zero, the full-window numbers are likely backtest artifacts.
      </p>

      {/* Strategy knobs.  Flipping any of these re-probes the endpoint; backend
          caches per-tuple so revisits are instant, first-time cold compute
          can take ~10-30s — the inline pill below the row indicates that. */}
      {onParamsChange && (
        <div style={{
          display: "flex", flexWrap: "wrap", alignItems: "center", gap: "20px",
          marginBottom: "16px", padding: "12px 14px",
          background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "2px",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.15em",
              color: "var(--muted)", textTransform: "uppercase",
            }}>Direction</span>
            <_AdminPill
              active={params?.direction_mode === "momentum"}
              onClick={() => onParamsChange({ ...params, direction_mode: "momentum" })}
              title="Long on bullish signals — buy what's trending"
            >Momentum</_AdminPill>
            <_AdminPill
              active={params?.direction_mode === "contrarian"}
              onClick={() => onParamsChange({ ...params, direction_mode: "contrarian" })}
              title="Long on bearish signals — buy the panic, fade the euphoria"
            >Contrarian</_AdminPill>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.15em",
              color: "var(--muted)", textTransform: "uppercase",
            }}>Signal</span>
            <_AdminPill
              active={params?.signal === "shift"}
              onClick={() => onParamsChange({ ...params, signal: "shift" })}
              title="Sentiment shifts past a fixed threshold vs 7d rolling mean"
            >Shift</_AdminPill>
            <_AdminPill
              active={params?.signal === "divergence"}
              onClick={() => onParamsChange({ ...params, signal: "divergence" })}
              title="7d sentiment moves against 7d price — classic mean-reversion setup"
            >Divergence</_AdminPill>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.15em",
              color: "var(--muted)", textTransform: "uppercase",
            }}>Hold</span>
            {[1, 3, 5, 7, 10].map(d => (
              <_AdminPill
                key={d}
                active={params?.hold_days === d}
                onClick={() => onParamsChange({ ...params, hold_days: d })}
                title={`Exit ${d} calendar day${d === 1 ? "" : "s"} after entry`}
              >{d}d</_AdminPill>
            ))}
          </div>
          {loading && (
            <span style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.15em",
              color: "var(--accent)", textTransform: "uppercase",
              marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: "6px",
            }}>
              <span style={{
                width: "6px", height: "6px", borderRadius: "50%",
                background: "var(--accent)", animation: "pulse 1.2s ease-in-out infinite",
              }} />
              Recomputing…
            </span>
          )}
        </div>
      )}

      {avgOosNet != null && (
        <div style={{
          marginBottom: "16px", padding: "16px 18px",
          background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "2px",
          display: "flex", gap: "32px", flexWrap: "wrap",
        }}>
          <div>
            <div style={{ fontSize: "10px", letterSpacing: "0.15em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "4px" }}>Avg OOS net (all tickers)</div>
            <div style={{ fontSize: "22px", fontWeight: 600, color: avgOosNet > 0 ? "var(--positive)" : avgOosNet < 0 ? "var(--negative)" : "var(--text)", fontFamily: "var(--mono)" }}>
              {avgOosNet > 0 ? "+" : ""}{avgOosNet.toFixed(2)}%
            </div>
          </div>
          <div>
            <div style={{ fontSize: "10px", letterSpacing: "0.15em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "4px" }}>Tickers with positive OOS net</div>
            <div style={{ fontSize: "22px", fontWeight: 600, color: "var(--text)", fontFamily: "var(--mono)" }}>
              {positiveCount} / {oosNets.length}
            </div>
          </div>
        </div>
      )}

      <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: "2px" }}>
        <table style={{
          width: "100%", borderCollapse: "collapse",
          fontFamily: "var(--mono)", fontSize: "12px",
        }}>
          <thead>
            <tr style={{ background: "var(--bg)", borderBottom: "1px solid var(--border)" }}>
              <th style={{ padding: "10px 12px", textAlign: "right", width: "48px",
                           color: "var(--muted)", fontWeight: 500, letterSpacing: "0.08em" }}>#</th>
              {cols.map(c => (
                <th key={c.key}
                    onClick={() => c.sort && onSort(c.key)}
                    title={c.hint}
                    style={{
                      padding: "10px 12px", textAlign: c.align, fontWeight: 500,
                      color: sortKey === c.key ? "var(--accent2)" : "var(--muted)",
                      letterSpacing: "0.08em", cursor: c.sort ? "pointer" : "default",
                      userSelect: "none", textTransform: "uppercase", fontSize: "10px",
                      whiteSpace: "nowrap",
                    }}>
                  {c.label}{sortKey === c.key ? " ↓" : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={r.ticker} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>{i + 1}</td>
                {cols.map(c => {
                  const val = c.val(r)
                  const colorVal = c.color ? c.color(r) : null
                  const color = colorVal == null ? "var(--text)"
                    : colorVal > 0 ? "var(--positive)"
                    : colorVal < 0 ? "var(--negative)"
                    : "var(--text)"
                  return (
                    <td key={c.key} style={{
                      padding: "10px 12px", textAlign: c.align, color,
                      fontWeight: c.bold ? 600 : 400, whiteSpace: "nowrap",
                    }}>{val}</td>
                  )
                })}
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr><td colSpan={cols.length + 1} style={{
                padding: "32px", textAlign: "center", color: "var(--muted)",
              }}>No backtest data yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {generated && (
        <div style={{
          marginTop: "12px", fontFamily: "var(--mono)", fontSize: "10px",
          color: "var(--muted)", letterSpacing: "0.05em", textAlign: "right",
        }}>
          Computed {generated.toLocaleString()} · cached 1h · append ?refresh=true to force
          {board?.costs_bps_override != null ? ` · costs override: ${board.costs_bps_override}bps` : ""}
        </div>
      )}
    </section>
  )
}

function Leaderboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState("all")
  // sortKey null = use backend default ordering; otherwise client-sort
  const [sortKey, setSortKey] = useState(null)
  const [sortDir, setSortDir] = useState("desc")
  // In-page auth — the Leaderboard now opens the same AuthModal the Dashboard
  // uses instead of bouncing users to /?auth=login (which dropped them on the
  // dashboard after login).  Subscribing to onAuthStateChange means the admin
  // probe below re-fires when an admin signs in without a page refresh.
  const [showAuth, setShowAuth] = useState(false)
  const [authMode, setAuthMode] = useState("login")
  const [session, setSession] = useState(null)
  // Admin-only backtest board.  Hidden by default; populated only when the
  // backend accepts the user's Supabase JWT and confirms they're on the
  // ADMIN_EMAILS allowlist.  A 401/403 leaves these null and the section
  // never renders — non-admins don't even know it exists.
  const [adminBoard, setAdminBoard] = useState(null)
  const [adminBoardLoading, setAdminBoardLoading] = useState(false)
  const [adminBoardError, setAdminBoardError] = useState(null)
  // Default to OOS-net sort — matches the backend's primary ordering and
  // surfaces the most honest single metric at the top of the table.
  const [adminSort, setAdminSort] = useState("oos_net_total")
  // Backtest parameter knobs.  Defaults mirror the backend's own defaults so
  // the first render of the board is identical to what hitting the endpoint
  // bare would return.  Toggling any of these re-probes the endpoint; the
  // backend caches per-tuple, so flipping between previously-seen configs is
  // instant.  Cold flips take ~10-30s — the "Recomputing…" indicator covers
  // that gap.
  const [adminParams, setAdminParams] = useState({
    direction_mode: "momentum",
    signal: "shift",
    hold_days: 7,
  })

  useEffect(() => {
    document.title = "Sentiment Leaderboard — 24h movers · SentimentFX"
    const setMeta = (name, content) => {
      let el = document.querySelector(`meta[name="${name}"]`)
      if (!el) { el = document.createElement("meta"); el.setAttribute("name", name); document.head.appendChild(el) }
      el.setAttribute("content", content)
    }
    setMeta("description",
      "Live FinBERT sentiment leaderboard for 42 assets across crypto, FX, stocks, ETFs and commodities. " +
      "Ranked by biggest 24h sentiment movers. Updated every 15 minutes.")
  }, [])

  useEffect(() => {
    let cancelled = false
    axios.get(`${API}/leaderboard`)
      .then(r => { if (!cancelled) setData(r.data) })
      .catch(e => { if (!cancelled) setError(e.message ?? "Failed to load") })
    return () => { cancelled = true }
  }, [])

  // Track session locally so the in-page auth modal can flip header chrome
  // without a refresh, AND so the admin probe below re-fires when an admin
  // signs in (the effect's session dep means it reruns on the token change).
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_evt, s) => setSession(s)
    )
    return () => subscription.unsubscribe()
  }, [])

  // Probe /admin/backtest-board with the current Supabase JWT.  If 200, the
  // user is on the email allowlist and we render the section.  Any 401/403/
  // network error silently leaves it hidden — there's no UI affordance that
  // would tip off a non-admin that the section exists.
  //
  // Re-fires on session change (login/logout) and on any adminParams flip
  // (direction/signal/hold from the control bar).  Backend caches per-tuple
  // so repeated visits to a previously-seen config are instant.
  useEffect(() => {
    let cancelled = false
    const token = session?.access_token
    if (!token) {
      setAdminBoard(null)
      setAdminBoardLoading(false)
      return   // logged out — no probe needed
    }
    setAdminBoardLoading(true)
    const qs = new URLSearchParams({
      direction_mode: adminParams.direction_mode,
      signal:         adminParams.signal,
      hold_days:      String(adminParams.hold_days),
    }).toString()
    axios.get(`${API}/admin/backtest-board?${qs}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => { if (!cancelled) { setAdminBoard(r.data); setAdminBoardLoading(false) } })
      .catch(e => {
        if (cancelled) return
        // 401/403 = not an admin → silently hide.  Anything else (5xx,
        // network) is a real error worth surfacing only if the section
        // would otherwise have rendered, so we keep adminBoard null too.
        const status = e?.response?.status
        if (status !== 401 && status !== 403) {
          setAdminBoardError(e?.message ?? "Failed to load")
        }
        setAdminBoardLoading(false)
      })
    return () => { cancelled = true }
  }, [session, adminParams])

  const openAuth = (mode) => { setAuthMode(mode); setShowAuth(true) }

  const onSort = (col) => {
    if (sortKey === col.key) {
      setSortDir(d => d === "asc" ? "desc" : "asc")
    } else {
      setSortKey(col.key)
      setSortDir("desc")
    }
  }

  const rows = (data?.rows ?? []).filter(r => filter === "all" ? true : r.category === filter)
  const sortedRows = (() => {
    if (!sortKey) return rows   // backend default = biggest abs Δ sentiment
    const col = LEADERBOARD_COLS.find(c => c.key === sortKey)
    if (!col) return rows
    const dir = sortDir === "asc" ? 1 : -1
    return [...rows].sort((a, b) => {
      const av = col.sort(a), bv = col.sort(b)
      if (typeof av === "string") return av.localeCompare(bv) * dir
      return (av - bv) * dir
    })
  })()

  return (
    <>
      <style>{styles}</style>
      <div className="dashboard">
        <header className="topbar">
          <div className="topbar-left">
            <a href="https://sentimentfx.org" className="logo" style={{ textDecoration: "none" }}>SentimentFX</a>
            <div className="logo-divider" />
            <span className="tagline">SENTIMENT LEADERBOARD</span>
          </div>
          <div className="topbar-right">
            <a href="/" style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
              color: "var(--muted)", textDecoration: "none",
            }}>DASHBOARD</a>
            <a href="/track-record" style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
              color: "var(--muted)", textDecoration: "none",
            }}>TRACK RECORD</a>
            <a href="https://developers.sentimentfx.org" target="_blank" rel="noreferrer" style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
              color: "var(--muted)", textDecoration: "none",
            }}>DEVELOPERS</a>
            {session ? (
              <a href="/" style={{
                fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                padding: "4px 10px", border: "1px solid var(--border2)", borderRadius: "2px",
                color: "var(--text)", textDecoration: "none",
              }}>{(session.user?.email ?? "ACCOUNT").slice(0, 18)} ↗</a>
            ) : (
              <>
                <button onClick={() => openAuth("login")} style={{
                  fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                  background: "transparent", border: "none", color: "var(--muted)",
                  cursor: "pointer", padding: 0,
                }}>LOG IN</button>
                <button onClick={() => openAuth("signup")} style={{
                  fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                  padding: "4px 10px", border: "1px solid var(--accent)", borderRadius: "2px",
                  background: "rgba(240,180,41,0.06)", color: "var(--accent)",
                  cursor: "pointer",
                }}>SIGN UP</button>
              </>
            )}
            <div className="live-indicator">
              <div className="live-dot" />
              LIVE
            </div>
          </div>
        </header>

        <main className="main" style={{ padding: "32px 24px 64px" }}>
          <div style={{ marginBottom: "24px" }}>
            <h1 style={{
              fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 500,
              color: "var(--text)", letterSpacing: "0.02em", marginBottom: "8px",
            }}>24-hour sentiment movers</h1>
            <p style={{
              fontFamily: "var(--sans)", fontSize: "13px", color: "var(--muted)",
              maxWidth: "680px", lineHeight: 1.5,
            }}>
              FinBERT-scored news sentiment across {data?.rows?.length ?? 42} tracked assets.
              Ranked by absolute change in volume-weighted sentiment over the last 24 hours
              vs. the prior 24 hours. Refreshed every 15 minutes from RSS, with prices in GBP.
            </p>
          </div>

          {/* Category filter — matches the dashboard switcher pattern */}
          <nav className="category-bar" style={{ marginBottom: "16px" }}>
            {CATEGORY_FILTERS.map(c => (
              <button
                key={c.key}
                className={`category-btn ${filter === c.key ? "active" : ""}`}
                onClick={() => setFilter(c.key)}
              >
                {c.label}
              </button>
            ))}
          </nav>

          {error && (
            <div style={{
              padding: "16px", border: "1px solid var(--negative)", borderRadius: "2px",
              fontFamily: "var(--mono)", fontSize: "12px", color: "var(--negative)",
            }}>
              Failed to load leaderboard: {error}
            </div>
          )}

          {!error && !data && (
            <div style={{
              padding: "32px", textAlign: "center", color: "var(--muted)",
              fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.08em",
            }}>LOADING…</div>
          )}

          {data && !error && (
            <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: "2px" }}>
              <table style={{
                width: "100%", borderCollapse: "collapse",
                fontFamily: "var(--mono)", fontSize: "12px",
              }}>
                <thead>
                  <tr style={{ background: "var(--surface)", borderBottom: "1px solid var(--border)" }}>
                    <th style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)", fontWeight: 500, letterSpacing: "0.08em", width: "48px" }}>#</th>
                    {LEADERBOARD_COLS.map(c => (
                      <th key={c.key}
                          onClick={() => onSort(c)}
                          style={{
                            padding: "10px 12px", textAlign: c.align, fontWeight: 500,
                            color: sortKey === c.key ? "var(--accent2)" : "var(--muted)",
                            letterSpacing: "0.08em", cursor: "pointer", userSelect: "none",
                          }}>
                        {c.label}{sortKey === c.key ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedRows.length === 0 && (
                    <tr><td colSpan={LEADERBOARD_COLS.length + 1} style={{
                      padding: "32px", textAlign: "center", color: "var(--muted)",
                    }}>No tickers in this category yet.</td></tr>
                  )}
                  {sortedRows.map((r, i) => {
                    const slug = TICKER_SLUGS[r.ticker]
                    const href = slug ? `https://sentimentfx.org/sentiment/${slug}.html` : "#"
                    return (
                      <tr key={r.ticker}
                          style={{
                            borderBottom: "1px solid var(--border)",
                            transition: "background 0.1s",
                          }}
                          onMouseOver={e => e.currentTarget.style.background = "var(--surface)"}
                          onMouseOut={e => e.currentTarget.style.background = "transparent"}>
                        <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>{i + 1}</td>
                        <td style={{ padding: "10px 12px" }}>
                          <a href={href} style={{
                            color: "var(--text)", textDecoration: "none", fontWeight: 500,
                          }}>{_displayLabel(r.ticker)}</a>
                          <span style={{ color: "var(--muted)", marginLeft: "8px", fontSize: "10px", letterSpacing: "0.08em" }}>
                            {r.category?.toUpperCase()}
                          </span>
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", color: _scoreColor(r.sentiment_24h) }}>
                          {_signedScore(r.sentiment_24h)}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", color: _scoreColor(r.sentiment_change_24h), fontWeight: 500 }}>
                          {_signedScore(r.sentiment_change_24h)}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>
                          {r.article_count_24h ?? 0}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--text)" }}>
                          {_formatPrice(r.price, r.ticker)}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", color: _scoreColor(r.price_change_24h_pct) }}>
                          {_signedPct(r.price_change_24h_pct)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Acquisition CTA — the whole point of this page being public */}
          <div style={{
            marginTop: "32px", padding: "20px 24px",
            border: "1px solid var(--border)", borderRadius: "2px",
            background: "var(--surface)", display: "flex",
            justifyContent: "space-between", alignItems: "center", gap: "16px", flexWrap: "wrap",
          }}>
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "13px", color: "var(--text)", marginBottom: "4px" }}>
                Get notified when these move.
              </div>
              <div style={{ fontFamily: "var(--sans)", fontSize: "12px", color: "var(--muted)" }}>
                Sentiment alerts, full chart history, and CSV export with a free account.
              </div>
            </div>
            <a href="/?auth=signup" style={{
              fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.08em",
              padding: "10px 18px", border: "1px solid var(--accent)", borderRadius: "2px",
              background: "var(--accent)", color: "var(--bg)", textDecoration: "none", fontWeight: 500,
            }}>SET UP ALERTS →</a>
          </div>

          {data?.generated_at && (
            <div style={{
              marginTop: "16px", fontFamily: "var(--mono)", fontSize: "10px",
              color: "var(--muted)", letterSpacing: "0.05em", textAlign: "right",
            }}>
              Updated {new Date(data.generated_at).toLocaleString()} · 15-min refresh cycle
            </div>
          )}

          {/* Admin-only backtest board.  Renders only after the backend has
              confirmed the logged-in user is on the ADMIN_EMAILS allowlist.
              Non-admins never see this section — no skeleton, no error toast. */}
          {adminBoard && (
            <AdminBacktestBoard
              board={adminBoard}
              sortKey={adminSort}
              onSort={setAdminSort}
              params={adminParams}
              onParamsChange={setAdminParams}
              loading={adminBoardLoading}
            />
          )}
        </main>
      </div>
      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialMode={authMode} />}
    </>
  )
}

// ─── Track Record (public, no auth) ─────────────────────────────────────────
// Lives at /track-record.  Pulls GET /track-record on mount.  The point of
// this page is conversion: a visitor sees real fired alerts with realised
// returns, not a backtest.  Empty state matters — a brand-new system has
// nothing settled yet, so we lead with the pending count instead of pretending
// to have a track record.  Cache via the public-data Cache-Control rule.

const TRACK_RECORD_WINDOWS = [30, 90, 180, 365]

function _fmtPct(v, dp = 2) {
  if (v === null || v === undefined) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(dp)}%`
}

function _fmtPctUnsigned(v, dp = 0) {
  if (v === null || v === undefined) return "—"
  return `${v.toFixed(dp)}%`
}

function _returnColor(v) {
  if (v === null || v === undefined) return "var(--muted)"
  if (v > 0) return "var(--positive)"
  if (v < 0) return "var(--negative)"
  return "var(--text)"
}

function _confidenceColor(c) {
  if (c === "high")   return "var(--positive)"
  if (c === "medium") return "var(--accent2)"
  if (c === "low")    return "var(--muted)"
  return "var(--muted)"
}

function _shortTicker(t) {
  return FX_LABELS[t] ?? COMMODITY_LABELS[t] ?? t
}

function _fmtDateShort(iso) {
  if (!iso) return "—"
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })
  } catch { return "—" }
}

function TrackRecordStatCard({ label, value, color, subtitle }) {
  return (
    <div style={{
      flex: "1 1 180px", minWidth: "160px",
      padding: "20px 22px",
      border: "1px solid var(--border)", borderRadius: "2px",
      background: "var(--surface)",
    }}>
      <div style={{
        fontSize: "10px", letterSpacing: "0.15em", textTransform: "uppercase",
        color: "var(--muted)", marginBottom: "8px", fontFamily: "var(--mono)",
      }}>{label}</div>
      <div style={{
        fontSize: "28px", fontWeight: 600, color: color || "var(--text)",
        fontFamily: "var(--mono)", lineHeight: 1.1,
      }}>{value}</div>
      {subtitle && (
        <div style={{
          fontSize: "11px", color: "var(--muted)", marginTop: "6px",
          fontFamily: "var(--sans)",
        }}>{subtitle}</div>
      )}
    </div>
  )
}

function TrackRecord() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [days, setDays] = useState(90)
  // In-page auth so visitors can log in / sign up without losing their place.
  const [showAuth, setShowAuth] = useState(false)
  const [authMode, setAuthMode] = useState("login")
  const [session, setSession] = useState(null)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_evt, s) => setSession(s)
    )
    return () => subscription.unsubscribe()
  }, [])

  const openAuth = (mode) => { setAuthMode(mode); setShowAuth(true) }

  useEffect(() => {
    document.title = "Track Record — live alert outcomes · SentimentFX"
    const setMeta = (name, content) => {
      let el = document.querySelector(`meta[name="${name}"]`)
      if (!el) { el = document.createElement("meta"); el.setAttribute("name", name); document.head.appendChild(el) }
      el.setAttribute("content", content)
    }
    setMeta("description",
      "Live track record of every SentimentFX alert that fired and the realised return when the hold period closed. " +
      "Not a backtest — actual trades the system recommended, settled against actual prices.")
  }, [])

  useEffect(() => {
    let cancelled = false
    setData(null)
    setError(null)
    axios.get(`${API}/track-record?days=${days}`)
      .then(r => { if (!cancelled) setData(r.data) })
      .catch(e => { if (!cancelled) setError(e.message ?? "Failed to load") })
    return () => { cancelled = true }
  }, [days])

  const overall = data?.overall
  const hasSettled = overall && overall.count > 0
  const totalReturn = overall?.total_return_pct
  const winRate = overall?.win_rate
  const avgReturn = overall?.avg_return_pct
  const pending = data?.pending_count ?? 0

  const byTickerRows = data?.by_ticker ?? []
  const recent = data?.recent ?? []

  return (
    <>
      <style>{styles}</style>
      <div className="dashboard">
        <header className="topbar">
          <div className="topbar-left">
            <a href="https://sentimentfx.org" className="logo" style={{ textDecoration: "none" }}>SentimentFX</a>
            <div className="logo-divider" />
            <span className="tagline">TRACK RECORD</span>
          </div>
          <div className="topbar-right">
            <a href="/" style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
              color: "var(--muted)", textDecoration: "none",
            }}>DASHBOARD</a>
            <a href="/leaderboard" style={{
              fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
              color: "var(--muted)", textDecoration: "none",
            }}>LEADERBOARD</a>
            {session ? (
              <a href="/" style={{
                fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                padding: "4px 10px", border: "1px solid var(--border2)", borderRadius: "2px",
                color: "var(--text)", textDecoration: "none",
              }}>{(session.user?.email ?? "ACCOUNT").slice(0, 18)} ↗</a>
            ) : (
              <>
                <button onClick={() => openAuth("login")} style={{
                  fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                  background: "transparent", border: "none", color: "var(--muted)",
                  cursor: "pointer", padding: 0,
                }}>LOG IN</button>
                <button onClick={() => openAuth("signup")} style={{
                  fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                  padding: "4px 10px", border: "1px solid var(--accent)", borderRadius: "2px",
                  background: "rgba(240,180,41,0.06)", color: "var(--accent)",
                  cursor: "pointer",
                }}>SIGN UP</button>
              </>
            )}
            <div className="live-indicator">
              <div className="live-dot" />
              LIVE
            </div>
          </div>
        </header>

        <main className="main" style={{ padding: "32px 24px 64px" }}>
          <div style={{ marginBottom: "20px" }}>
            <h1 style={{
              fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 500,
              color: "var(--text)", letterSpacing: "0.02em", marginBottom: "8px",
            }}>Receipts, not promises.</h1>
            <p style={{
              fontFamily: "var(--sans)", fontSize: "13px", color: "var(--muted)",
              maxWidth: "720px", lineHeight: 1.5,
            }}>
              Every time the system fires a trade alert it gets logged here with the entry price.
              When the hold period closes, the exit price and signed return are filled in automatically.
              This is the realised performance of the alerts users have actually received —
              not a backtest, not a simulation. Aim a critical eye at it.
            </p>
          </div>

          {/* Window switcher */}
          <nav className="category-bar" style={{ marginBottom: "20px" }}>
            {TRACK_RECORD_WINDOWS.map(w => (
              <button
                key={w}
                className={`category-btn ${days === w ? "active" : ""}`}
                onClick={() => setDays(w)}
              >
                {w}d
              </button>
            ))}
          </nav>

          {error && (
            <div style={{
              padding: "16px", border: "1px solid var(--negative)", borderRadius: "2px",
              fontFamily: "var(--mono)", fontSize: "12px", color: "var(--negative)",
            }}>
              Failed to load track record: {error}
            </div>
          )}

          {!error && !data && (
            <div style={{
              padding: "32px", textAlign: "center", color: "var(--muted)",
              fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.08em",
            }}>LOADING…</div>
          )}

          {data && !error && (
            <>
              {/* Hero stat cards.  In the empty state (no settled trades), we
                  lead with the pending count rather than fake a zero.  When
                  there ARE settled trades we lead with compounded total return
                  because that's the trader-language headline number. */}
              <div style={{
                display: "flex", flexWrap: "wrap", gap: "12px", marginBottom: "20px",
              }}>
                {hasSettled ? (
                  <>
                    <TrackRecordStatCard
                      label="Compounded return"
                      value={_fmtPct(totalReturn)}
                      color={_returnColor(totalReturn)}
                      subtitle={`Across ${overall.count} settled trade${overall.count === 1 ? "" : "s"}, last ${days}d`}
                    />
                    <TrackRecordStatCard
                      label="Win rate"
                      value={winRate == null ? "—" : `${Math.round(winRate * 100)}%`}
                      subtitle={`${Math.round((winRate ?? 0) * overall.count)} winners of ${overall.count}`}
                    />
                    <TrackRecordStatCard
                      label="Avg return per trade"
                      value={_fmtPct(avgReturn)}
                      color={_returnColor(avgReturn)}
                      subtitle="Equal-weighted, not compounded"
                    />
                    <TrackRecordStatCard
                      label="Currently open"
                      value={pending}
                      subtitle="Alerts fired, hold not yet closed"
                    />
                  </>
                ) : (
                  <>
                    <TrackRecordStatCard
                      label="Settled trades"
                      value="0"
                      subtitle={`No alerts have closed their hold window in the last ${days}d yet`}
                    />
                    <TrackRecordStatCard
                      label="Currently open"
                      value={pending}
                      subtitle={pending === 0
                        ? "No alerts have fired in this window"
                        : "Alerts fired, waiting on settlement"}
                    />
                    <TrackRecordStatCard
                      label="Track record is honest"
                      value="↗"
                      color="var(--accent)"
                      subtitle="Every fired alert gets logged with entry price the second it sends. We can't curate."
                    />
                  </>
                )}
              </div>

              {hasSettled && (
                <>
                  {/* Breakdown: direction × confidence — side by side */}
                  <div style={{
                    display: "grid", gap: "12px", marginBottom: "24px",
                    gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                  }}>
                    <BreakdownTable
                      title="By direction"
                      hint="Long-only and short-only alerts split out"
                      rows={[
                        { label: "LONG",  agg: data.by_direction?.LONG },
                        { label: "SHORT", agg: data.by_direction?.SHORT },
                      ]}
                    />
                    <BreakdownTable
                      title="By confidence"
                      hint="High-confidence alerts should outperform low — if they don't, the signal isn't calibrated"
                      rows={[
                        { label: "high",   agg: data.by_confidence?.high,   color: _confidenceColor("high") },
                        { label: "medium", agg: data.by_confidence?.medium, color: _confidenceColor("medium") },
                        { label: "low",    agg: data.by_confidence?.low,    color: _confidenceColor("low") },
                      ]}
                    />
                  </div>

                  {/* Per-ticker breakdown — only show if there are at least 2 */}
                  {byTickerRows.length >= 2 && (
                    <div style={{
                      marginBottom: "24px", border: "1px solid var(--border)",
                      borderRadius: "2px", background: "var(--surface)",
                    }}>
                      <div style={{
                        padding: "14px 18px", borderBottom: "1px solid var(--border)",
                      }}>
                        <div style={{
                          fontFamily: "var(--mono)", fontSize: "13px", color: "var(--text)",
                          marginBottom: "2px",
                        }}>By asset</div>
                        <div style={{
                          fontFamily: "var(--sans)", fontSize: "11px", color: "var(--muted)",
                        }}>Sorted by win rate. Tickers with single trades are kept visible — sample size is the asterisk.</div>
                      </div>
                      <div style={{ overflowX: "auto" }}>
                        <table style={{
                          width: "100%", borderCollapse: "collapse",
                          fontFamily: "var(--mono)", fontSize: "12px",
                        }}>
                          <thead>
                            <tr style={{ background: "var(--bg)", borderBottom: "1px solid var(--border)" }}>
                              <th style={_thStyle("left")}>Asset</th>
                              <th style={_thStyle("right")}>Trades</th>
                              <th style={_thStyle("right")}>Win rate</th>
                              <th style={_thStyle("right")}>Avg ret</th>
                              <th style={_thStyle("right")}>Compounded</th>
                            </tr>
                          </thead>
                          <tbody>
                            {byTickerRows.map(r => (
                              <tr key={r.ticker} style={{ borderBottom: "1px solid var(--border)" }}>
                                <td style={{ padding: "10px 12px" }}>
                                  <span style={{ color: "var(--text)" }}>{_shortTicker(r.ticker)}</span>
                                  <span style={{ color: "var(--muted)", marginLeft: "8px", fontSize: "10px", letterSpacing: "0.08em" }}>
                                    {(r.category ?? "").toUpperCase()}
                                  </span>
                                </td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>{r.count}</td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--text)" }}>
                                  {r.win_rate == null ? "—" : `${Math.round(r.win_rate * 100)}%`}
                                </td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: _returnColor(r.avg_return_pct) }}>
                                  {_fmtPct(r.avg_return_pct)}
                                </td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: _returnColor(r.total_return_pct), fontWeight: 500 }}>
                                  {_fmtPct(r.total_return_pct)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Recent settled trades */}
                  {recent.length > 0 && (
                    <div style={{
                      marginBottom: "24px", border: "1px solid var(--border)",
                      borderRadius: "2px", background: "var(--surface)",
                    }}>
                      <div style={{
                        padding: "14px 18px", borderBottom: "1px solid var(--border)",
                      }}>
                        <div style={{
                          fontFamily: "var(--mono)", fontSize: "13px", color: "var(--text)",
                          marginBottom: "2px",
                        }}>Last {recent.length} settled trades</div>
                        <div style={{
                          fontFamily: "var(--sans)", fontSize: "11px", color: "var(--muted)",
                        }}>Most recent first. Entry = close at fire time, exit = close after the hold window.</div>
                      </div>
                      <div style={{ overflowX: "auto" }}>
                        <table style={{
                          width: "100%", borderCollapse: "collapse",
                          fontFamily: "var(--mono)", fontSize: "12px",
                        }}>
                          <thead>
                            <tr style={{ background: "var(--bg)", borderBottom: "1px solid var(--border)" }}>
                              <th style={_thStyle("left")}>Fired</th>
                              <th style={_thStyle("left")}>Asset</th>
                              <th style={_thStyle("left")}>Direction</th>
                              <th style={_thStyle("left")}>Conf</th>
                              <th style={_thStyle("right")}>Hold</th>
                              <th style={_thStyle("right")}>Entry</th>
                              <th style={_thStyle("right")}>Exit</th>
                              <th style={_thStyle("right")}>Return</th>
                            </tr>
                          </thead>
                          <tbody>
                            {recent.map((r, i) => (
                              <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                                <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{_fmtDateShort(r.fired_at)}</td>
                                <td style={{ padding: "10px 12px", color: "var(--text)" }}>{_shortTicker(r.ticker)}</td>
                                <td style={{ padding: "10px 12px", color: r.direction === "LONG" ? "var(--positive)" : "var(--negative)" }}>
                                  {r.direction}
                                </td>
                                <td style={{ padding: "10px 12px", color: _confidenceColor(r.confidence) }}>
                                  {r.confidence ?? "—"}
                                </td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>{r.hold_days}d</td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>
                                  {r.entry_price == null ? "—" : _formatPrice(r.entry_price, r.ticker)}
                                </td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>
                                  {r.exit_price == null ? "—" : _formatPrice(r.exit_price, r.ticker)}
                                </td>
                                <td style={{ padding: "10px 12px", textAlign: "right", color: _returnColor(r.return_pct), fontWeight: 500 }}>
                                  {_fmtPct(r.return_pct)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Methodology — second-class but important.  Trust comes from
                  showing your work, not from hiding it. */}
              <details style={{
                marginBottom: "24px", padding: "16px 18px",
                border: "1px solid var(--border)", borderRadius: "2px",
                background: "var(--surface)", fontFamily: "var(--sans)", fontSize: "12px",
                color: "var(--muted)", lineHeight: 1.6,
              }}>
                <summary style={{
                  cursor: "pointer", fontFamily: "var(--mono)", fontSize: "11px",
                  letterSpacing: "0.08em", color: "var(--text)", textTransform: "uppercase",
                  marginBottom: "8px",
                }}>How this is calculated</summary>
                <p style={{ marginTop: "10px" }}>
                  <strong style={{ color: "var(--text)" }}>Entry.</strong> The instant a user's
                  sentiment alert fires, we generate a trade card (LONG / SHORT / NEUTRAL) from the
                  current divergence between sentiment momentum and price.  If the card recommends
                  a trade, we snapshot the latest close as the entry price.
                </p>
                <p style={{ marginTop: "10px" }}>
                  <strong style={{ color: "var(--text)" }}>Exit.</strong> Each alert carries a hold
                  window (typically 7 days).  After that elapses, a daily settlement job records the
                  next available close as the exit price and computes the signed return
                  (positive for LONG when price rose, positive for SHORT when price fell).
                </p>
                <p style={{ marginTop: "10px" }}>
                  <strong style={{ color: "var(--text)" }}>What's excluded.</strong> NEUTRAL cards
                  aren't logged — there's nothing to settle.  Unsettled alerts are reported
                  separately as "currently open" and excluded from win-rate / return math so the
                  headline number can't be juiced by cherry-picking recent regime moves.
                </p>
                <p style={{ marginTop: "10px" }}>
                  <strong style={{ color: "var(--text)" }}>Not financial advice.</strong> Past
                  results do not predict future performance.  Transaction costs are not modelled
                  on this page — see the admin backtest board for a costs-net view.
                </p>
              </details>
            </>
          )}

          {/* Conversion CTA */}
          <div style={{
            marginTop: "16px", padding: "20px 24px",
            border: "1px solid var(--border)", borderRadius: "2px",
            background: "var(--surface)", display: "flex",
            justifyContent: "space-between", alignItems: "center", gap: "16px", flexWrap: "wrap",
          }}>
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: "13px", color: "var(--text)", marginBottom: "4px" }}>
                {hasSettled
                  ? "Get the next one in your inbox."
                  : "Be the first alert on the board."}
              </div>
              <div style={{ fontFamily: "var(--sans)", fontSize: "12px", color: "var(--muted)" }}>
                Set a sentiment alert. Every fire is logged here, settled, and shown publicly.
              </div>
            </div>
            <a href="/?auth=signup" style={{
              fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.08em",
              padding: "10px 18px", border: "1px solid var(--accent)", borderRadius: "2px",
              background: "var(--accent)", color: "var(--bg)", textDecoration: "none", fontWeight: 500,
            }}>SET UP ALERTS →</a>
          </div>

          {data?.generated_at && (
            <div style={{
              marginTop: "16px", fontFamily: "var(--mono)", fontSize: "10px",
              color: "var(--muted)", letterSpacing: "0.05em", textAlign: "right",
            }}>
              Computed {new Date(data.generated_at).toLocaleString()} · {days}d window · settled-only
            </div>
          )}
        </main>
      </div>
      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialMode={authMode} />}
    </>
  )
}

const _thStyle = (align) => ({
  padding: "10px 12px", textAlign: align, fontWeight: 500,
  color: "var(--muted)", letterSpacing: "0.08em",
  textTransform: "uppercase", fontSize: "10px", whiteSpace: "nowrap",
})

function BreakdownTable({ title, hint, rows }) {
  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: "2px", background: "var(--surface)",
    }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)" }}>
        <div style={{
          fontFamily: "var(--mono)", fontSize: "13px", color: "var(--text)", marginBottom: "2px",
        }}>{title}</div>
        {hint && (
          <div style={{ fontFamily: "var(--sans)", fontSize: "11px", color: "var(--muted)" }}>{hint}</div>
        )}
      </div>
      <table style={{
        width: "100%", borderCollapse: "collapse",
        fontFamily: "var(--mono)", fontSize: "12px",
      }}>
        <thead>
          <tr style={{ background: "var(--bg)", borderBottom: "1px solid var(--border)" }}>
            <th style={_thStyle("left")}>Slice</th>
            <th style={_thStyle("right")}>n</th>
            <th style={_thStyle("right")}>Win rate</th>
            <th style={_thStyle("right")}>Avg ret</th>
            <th style={_thStyle("right")}>Compounded</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ label, agg, color }) => {
            const empty = !agg || !agg.count
            return (
              <tr key={label} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "10px 12px", color: color || "var(--text)", textTransform: "uppercase", fontSize: "11px", letterSpacing: "0.06em" }}>
                  {label}
                </td>
                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--muted)" }}>{empty ? 0 : agg.count}</td>
                <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--text)" }}>
                  {empty || agg.win_rate == null ? "—" : `${Math.round(agg.win_rate * 100)}%`}
                </td>
                <td style={{ padding: "10px 12px", textAlign: "right", color: _returnColor(empty ? null : agg.avg_return_pct) }}>
                  {empty ? "—" : _fmtPct(agg.avg_return_pct)}
                </td>
                <td style={{ padding: "10px 12px", textAlign: "right", color: _returnColor(empty ? null : agg.total_return_pct), fontWeight: 500 }}>
                  {empty ? "—" : _fmtPct(agg.total_return_pct)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// Top-level dispatcher.  Keeps the hooks rule clean: the dashboard hooks only
// run when we're actually rendering Dashboard, and Leaderboard's hooks only
// run when we're on /leaderboard — no conditional hook calls inside either.
export default function App() {
  const pathname = typeof window !== "undefined" ? window.location.pathname : "/"
  if (pathname === "/leaderboard")   return <Leaderboard />
  if (pathname === "/track-record")  return <TrackRecord />
  return <Dashboard />
}

function Dashboard() {
  const [ticker, setTicker] = useState("BTC")
  const [category, setCategory] = useState("Crypto")
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
  const [showAccount, setShowAccount] = useState(false)
  const [showPasswordReset, setShowPasswordReset] = useState(false)
  const [newPassword, setNewPassword] = useState("")
  const [passwordResetLoading, setPasswordResetLoading] = useState(false)
  const [passwordResetDone, setPasswordResetDone] = useState(false)
  const [alerts, setAlerts] = useState([])
  const [alertTicker, setAlertTicker] = useState("BTC")
  const [alertThreshold, setAlertThreshold] = useState(0.3)
  const [alertDirection, setAlertDirection] = useState("above")
  const [alertLoading, setAlertLoading] = useState(false)
  const [signalData, setSignalData] = useState(null)
  const [divergenceData, setDivergenceData] = useState(null)
  const [apiKeyInfo, setApiKeyInfo] = useState(null)
  const [apiKeyFull, setApiKeyFull] = useState(null)
  const [apiKeyLoading, setApiKeyLoading] = useState(false)
  const [apiKeyCopied, setApiKeyCopied] = useState(false)

  const urlParams = new URLSearchParams(window.location.search)
  const checkoutSuccess = urlParams.get("success")
  const checkoutCancelled = urlParams.get("cancelled")

  const isPro = profile?.tier === "pro" || profile?.tier === "data"
  const isFX = FX_TICKERS.includes(ticker)
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

  const todaySignal = signalData?.today ? {
    direction: signalData.today.sentiment_label.includes("bear") ? "BEARISH"
      : signalData.today.sentiment_label.includes("bull") ? "BULLISH"
      : "NEUTRAL",
    sentimentLabel: signalData.today.sentiment_label,
    score: signalData.today.sentiment,
    strength: getPrimary(correlation)?.strength ?? "inconclusive",
    isMomentum: getPrimary(correlation)?.direction?.includes("momentum") ?? null,
    correlation: getPrimary(correlation)?.correlation ?? null,
    beatsMomentum: correlation?.baseline?.primary_beats_momentum ?? null,
    sampleSize: correlation?.sample_size ?? null,
    narrative: signalData.summary,
    shift: signalData.today.shift,
    shiftPercentile: signalData.today.shift_percentile,
    shiftMagnitude: signalData.today.shift_magnitude,
    articleCount: signalData.today.article_count,
    daysSinceSimilar: signalData.context?.days_since_similar_shift ?? null,
  } : null

  // Convenience accessors for the new correlation shape
  const primary = getPrimary(correlation)
  const secondary = correlation?.secondary_signals ?? null
  const baseline = correlation?.baseline ?? null

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
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      setUser(session?.user ?? null)
      setSession(session ?? null)
      if (event === "PASSWORD_RECOVERY") {
        setShowPasswordReset(true)
      }
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
      .then(({ data }) => setProfile(data ?? {}))
  }, [user])

  useEffect(() => {
    fetchDashboard(range, 1, ticker)
  }, [ticker])

  useEffect(() => {
  const interval = setInterval(() => {
    fetchDashboard(range, 1, ticker)
  }, 5 * 60 * 1000)
  return () => clearInterval(interval)
  }, [ticker, range])

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
    if (isPro && session) {
      fetchAlerts()
      fetchApiKeyInfo()
    }
  }, [isPro, session])

  const fetchApiKeyInfo = async () => {
    if (!session) return
    try {
      const res = await fetch(`${API}/api/keys/me`, {
        headers: { Authorization: `Bearer ${session.access_token}` }
      })
      if (res.ok) setApiKeyInfo(await res.json())
    } catch (_) {}
  }

  const generateApiKey = async () => {
    setApiKeyLoading(true)
    try {
      const res = await fetch(`${API}/api/keys/generate-linked`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` }
      })
      const data = await res.json()
      if (res.ok) {
        setApiKeyFull(data.key)
        await fetchApiKeyInfo()
      }
    } finally {
      setApiKeyLoading(false)
    }
  }

  const regenerateApiKey = async () => {
    if (!window.confirm("This will immediately invalidate your current key. Continue?")) return
    setApiKeyLoading(true)
    try {
      const res = await fetch(`${API}/api/keys/regenerate-linked`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` }
      })
      const data = await res.json()
      if (res.ok) {
        setApiKeyFull(data.key)
        await fetchApiKeyInfo()
      }
    } finally {
      setApiKeyLoading(false)
    }
  }

  const copyApiKey = async () => {
    if (!apiKeyFull) return
    await navigator.clipboard.writeText(apiKeyFull)
    setApiKeyCopied(true)
    setTimeout(() => setApiKeyCopied(false), 2000)
  }

  const fetchAlerts = async () => {
    if (!isPro || !session) return
    try {
      const res = await fetch(`${API}/alerts`, {
        headers: { Authorization: `Bearer ${session.access_token}` }
      })
      if (!res.ok) {
        setAlerts([])
        return
      }
      const data = await res.json()
      setAlerts(Array.isArray(data) ? data : [])
    } catch (e) {
      console.error("Failed to fetch alerts:", e)
      setAlerts([])
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

      setSignalData(null)
      try {
        const sigRes = await axios.get(`${API}/signal/${selectedTicker}`)
        setSignalData(sigRes.data)
      } catch (e) {
        console.error("Signal fetch error:", e)
      }

      setDivergenceData(null)
      try {
        const divRes = await axios.get(`${API}/divergence/${selectedTicker}`)
        setDivergenceData(divRes.data)
      } catch (e) {
        console.error("Divergence fetch error:", e)
      }
    } catch (err) {
      console.error(err)
    }
    setLoading(false)
  }

  const profileLoading = !!user && !profile
  const handleTickerClick = (t) => {
    const isLocked = !profileLoading && !FREE_TICKERS.includes(t) && !isPro
    if (isLocked) { setAuthMode("signup"); setShowAuth(true); return }
    setTicker(t)
  }

  const filteredData = (() => {
    if (!isPro && range > 30) return allData.slice(-30)
    return range === 999 ? allData : allData.slice(-range)
  })()

  const rate = !isFX && currency === "USD" && gbpToUsd ? gbpToUsd : 1
  const symbol = isFX ? "" : (currency === "USD" ? "$" : "£")

  const displayData = filteredData.map(d => ({
    ...d,
    price: d.price != null ? parseFloat((d.price * rate).toFixed(2)) : null,
  }))

  const sentimentSignal = avgSentiment !== null
    ? avgSentiment > 0.1 ? "BULLISH" : avgSentiment < -0.1 ? "BEARISH" : "NEUTRAL"
    : null

  const latestPrice = [...displayData].reverse().find(d => d.price != null)?.price ?? null
  const priceDisplay = latestPrice != null
    ? isFX
      ? latestPrice.toFixed(4)
      : `${symbol}${latestPrice >= 1000 ? latestPrice.toLocaleString() : latestPrice.toFixed(2)}`
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
    if (isFX) return v.toFixed(4)
    if (v >= 1000) return `${symbol}${(v / 1000).toFixed(0)}k`
    return `${symbol}${v.toFixed(2)}`
  }

  const tierBadgeClass = profile?.tier === "pro"
    ? "tier-badge tier-pro"
    : profile?.tier === "data"
    ? "tier-badge tier-data"
    : "tier-badge tier-free"

  // Stat card values for the new shape
  const statCorrValue = primary?.correlation ?? null
  const statCorrSub = primary
    ? `${primary.strength ?? "—"} · n=${correlation?.sample_size ?? "?"}`
    : "Loading..."

  return (
    <>
      <style>{styles}</style>
      <div className="dashboard">
        <header className="topbar">
          <div className="topbar-left">
            <a href="https://sentimentfx.org" className="logo" style={{ textDecoration: "none" }}>SentimentFX</a>
            <div className="logo-divider" />
            <span className="tagline">CRYPTO SENTIMENT INTELLIGENCE</span>
          </div>
          <div className="topbar-right">
            <a
              href="/leaderboard"
              style={{
                fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                color: "var(--muted)", textDecoration: "none", transition: "color 0.15s"
              }}
              onMouseOver={e => e.currentTarget.style.color = "var(--text)"}
              onMouseOut={e => e.currentTarget.style.color = "var(--muted)"}
            >
              LEADERBOARD
            </a>
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
                <button
                  onClick={() => setShowAccount(true)}
                  style={{
                    fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.08em",
                    padding: "4px 10px", border: "1px solid var(--border)", borderRadius: "2px",
                    cursor: "pointer", background: "transparent", color: "var(--muted)",
                  }}
                >
                  ACCOUNT
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

        <nav className="category-bar">
          {Object.keys(CATEGORIES).map(cat => (
            <button
              key={cat}
              className={`category-btn ${category === cat ? "active" : ""}`}
              onClick={() => {
                setCategory(cat)
                if (!CATEGORIES[cat].includes(ticker)) {
                  handleTickerClick(CATEGORIES[cat][0])
                }
              }}
            >
              {cat}
            </button>
          ))}
        </nav>

        <nav className="ticker-bar">
          {CATEGORIES[category].map(t => {
            const locked = !profileLoading && !FREE_TICKERS.includes(t) && !isPro
            return (
              <button
                key={t}
                className={`ticker-btn ${ticker === t ? "active" : ""} ${locked ? "locked" : ""}`}
                onClick={() => handleTickerClick(t)}
              >
                {FX_LABELS[t] ?? COMMODITY_LABELS[t] ?? t}
              </button>
            )
          })}
        </nav>

        <main className="main">

          {!isLoggedIn && (
            <div className="upgrade-banner">
              <span className="upgrade-text">
                <strong>Free tier:</strong> BTC only · 30 day history · Sign in to unlock 42 tickers across crypto, FX, stocks, ETFs and commodities, full history, API access and alerts.
              </span>
              <button className="upgrade-btn" onClick={() => { setAuthMode("signup"); setShowAuth(true) }}>
                Sign In / Sign Up
              </button>
            </div>
          )}

          {isLoggedIn && !isPro && (
            <div className="upgrade-banner">
              <span className="upgrade-text">
                <strong>Free tier:</strong> BTC only · 30 day history · Upgrade to Pro for 42 tickers across crypto, FX, stocks, ETFs and commodities, full history, 1,000 API calls/mo, alerts and morning brief.
              </span>
              <div style={{ display: "flex", gap: "8px" }}>
                <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TNx0H2NzVdYK0wrPwt0Rhcw")}>
                  £11.99 / mo
                </button>
                <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TNx0K2NzVdYK0wrcGf1mz1s")}>
                  £99.99 / yr
                </button>
              </div>
            </div>
          )}

          {isPro && !isData && (
            <div className="upgrade-banner" style={{ borderColor: "rgba(88,166,255,0.3)", background: "rgba(88,166,255,0.04)" }}>
              <span className="upgrade-text">
                <strong style={{ color: "var(--accent2)" }}>Pro plan active.</strong> Upgrade to Data for 5,000 API calls/mo included.
              </span>
              <div style={{ display: "flex", gap: "8px" }}>
                <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TUqVG2NzVdYK0wrKrPTE28e")}>
                  £49.99 / mo
                </button>
                <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TUqVx2NzVdYK0wryheortJg")}>
                  £499.99 / yr
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
                {isFX ? "exchange rate" : currency === "GBP" ? "British pound" : `USD · rate: ${gbpToUsd?.toFixed(4) ?? "..."}`}
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Correlation (r)</div>
              <div className={`stat-value ${!loading && statCorrValue !== null && statCorrValue < 0 ? "negative-text" : "positive-text"}`}>
                {loading
                  ? <span className="skeleton" style={{ display: "inline-block", width: "60px", height: "22px", borderRadius: "2px" }} />
                  : (statCorrValue ?? "—")}
              </div>

              <div className="stat-sub">{loading ? "—" : statCorrSub}</div>
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
                        {sentimentTrend.delta !== null ? ` ${Math.abs(sentimentTrend.delta)}` : ""}
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
              {[7, 30, 90].map(d => (
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
                  {`${d}D`}
                </button>
              ))}
            </div>
          )}

          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">{FX_LABELS[ticker] ?? ticker} / SENTIMENT vs PRICE{isFX ? "" : ` (${currency})`}</span>
              <div className="panel-controls">
                {!isFX && ["GBP", "USD"].map(c => (
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
            <div className="panel correlation-panel" style={{ gridColumn: "1" }}>
              <div className="panel-header">
                <span className="panel-title">PREDICTIVE SIGNAL</span>
                {correlation?.window_days && (
                  <span className="panel-title" style={{ color: "var(--muted)" }}>
                    {correlation.window_days}D WINDOW · n={correlation.sample_size}
                  </span>
                )}
              </div>
              {loading ? <CorrelationSkeleton /> : (
                <div className="panel-body">
                  {primary ? (
                    <div className="correlation-detail">
                      {/* ── Plain-English summary ───────────────────────── */}
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

                      {correlation?.interpretation && (
                        <div style={{ marginBottom: "12px" }}>
                          <strong>{correlation.interpretation}</strong>
                        </div>
                      )}

                      {/* ── Primary signal stats ─────────────────────────── */}
                      <div className="stat-block">
                        <div style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.1em", color: "var(--accent)", textTransform: "uppercase", marginBottom: "8px" }}>
                          Primary signal · sentiment shift → next-day return
                        </div>
                        <div className="stat-block-row">
                          <span className="stat-block-key">Correlation</span>
                          <span className="stat-block-val" style={{ color: primary.correlation < 0 ? "var(--negative)" : "var(--positive)" }}>
                            {primary.correlation > 0 ? "+" : ""}{primary.correlation}
                          </span>
                        </div>
                        <div className="stat-block-row">
                          <span className="stat-block-key">P-value</span>
                          <span className="stat-block-val" style={{ color: primary.p_value < 0.05 ? "var(--positive)" : primary.p_value < 0.10 ? "var(--accent)" : "var(--muted)" }}>
                            {primary.p_value}
                          </span>
                        </div>
                        {primary.ci_95 && (
                          <div className="stat-block-row">
                            <span className="stat-block-key">95% CI</span>
                            <span className="stat-block-val">
                              [{primary.ci_95[0]}, {primary.ci_95[1]}]
                            </span>
                          </div>
                        )}
                        <div className="stat-block-row">
                          <span className="stat-block-key">Strength</span>
                          <span className="stat-block-val" style={{
                            color: primary.strength === "strong" ? "var(--positive)" :
                                   primary.strength === "weak" ? "var(--accent)" :
                                   "var(--muted)"
                          }}>
                            {primary.strength?.toUpperCase()}
                          </span>
                        </div>
                        <div className="stat-block-row">
                          <span className="stat-block-key">Direction</span>
                          <span className="stat-block-val">
                            {primary.direction?.includes("momentum") ? "📈 Momentum" : "📉 Contrarian"}
                          </span>
                        </div>
                      </div>

                      {/* ── Secondary signals ────────────────────────────── */}
                      {secondary && (
                        <div className="stat-block">
                          <div style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "8px" }}>
                            Secondary signals
                          </div>
                          {secondary.sentiment_level_vs_next_day_return?.correlation !== null && (
                            <div className="stat-block-row">
                              <span className="stat-block-key">Sentiment level</span>
                              <span className="stat-block-val">
                                r = {secondary.sentiment_level_vs_next_day_return.correlation > 0 ? "+" : ""}{secondary.sentiment_level_vs_next_day_return.correlation}
                                <span style={{ color: "var(--muted)", marginLeft: "8px" }}>
                                  p={secondary.sentiment_level_vs_next_day_return.p_value}
                                </span>
                              </span>
                            </div>
                          )}
                          {secondary.news_volume_vs_next_day_return?.correlation !== null && (
                            <div className="stat-block-row">
                              <span className="stat-block-key">News volume</span>
                              <span className="stat-block-val">
                                r = {secondary.news_volume_vs_next_day_return.correlation > 0 ? "+" : ""}{secondary.news_volume_vs_next_day_return.correlation}
                                <span style={{ color: "var(--muted)", marginLeft: "8px" }}>
                                  p={secondary.news_volume_vs_next_day_return.p_value}
                                </span>
                              </span>
                            </div>
                          )}
                        </div>
                      )}

                      {/* ── Baseline comparison ──────────────────────────── */}
                      {baseline && baseline.momentum_autocorrelation !== null && (
                        <div className="stat-block">
                          <div style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "8px" }}>
                            Baseline comparison
                          </div>
                          <div className="stat-block-row">
                            <span className="stat-block-key">Momentum (yesterday → today)</span>
                            <span className="stat-block-val">
                              r = {baseline.momentum_autocorrelation > 0 ? "+" : ""}{baseline.momentum_autocorrelation}
                            </span>
                          </div>
                          <div className="stat-block-row">
                            <span className="stat-block-key">Beats baseline?</span>
                            <span className="stat-block-val" style={{
                              color: baseline.primary_beats_momentum ? "var(--positive)" : "var(--negative)"
                            }}>
                              {baseline.primary_beats_momentum ? "✓ YES" : "✗ NO"}
                            </span>
                          </div>
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
                        💡 The primary signal measures how much sentiment <em>shifts</em> (deviation from 7-day average) predict the <em>next day's</em> price return. Strength accounts for both effect size and statistical significance.
                      </div>
                    </div>
                  ) : (
                    <div style={{ padding: "16px", fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)" }}>
                      {correlation?.message ?? "Not enough data yet."}
                    </div>
                  )}
                </div>
              )}
            </div>

            <DivergenceCard data={divergenceData} loading={loading} />
          </div>

          <BacktestPanel ticker={ticker} />

          <HeadlineImpactPanel ticker={ticker} />

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
                  <div className="explainer-label" style={{ color: "var(--accent2)" }}>What is Sentiment Shift?</div>
                  <div className="explainer-text">
                    The shift is today's sentiment minus the 7-day rolling average. Markets price in steady-state sentiment — what tends to move price is <em>change</em>, not absolute level. We correlate shifts against next-day returns.
                  </div>
                </div>
                <div className="explainer-card" style={{ borderLeft: "2px solid var(--positive)" }}>
                  <div className="explainer-label" style={{ color: "var(--positive)" }}>Strength &amp; P-value</div>
                  <div className="explainer-text">
                    <span style={{ color: "var(--text)" }}>Strong</span> means a meaningful effect with strong statistical evidence. <span style={{ color: "var(--text)" }}>Weak</span> means a real but smaller effect. <span style={{ color: "var(--text)" }}>Inconclusive</span> means we cannot rule out chance.
                  </div>
                </div>
                <div className="explainer-card" style={{ borderLeft: "2px solid var(--neutral)" }}>
                  <div className="explainer-label" style={{ color: "var(--neutral)" }}>Momentum vs Contrarian</div>
                  <div className="explainer-text">
                    <span style={{ color: "var(--positive)" }}>Momentum</span> means positive sentiment shifts tend to be followed by price rises. <span style={{ color: "var(--negative)" }}>Contrarian</span> means positive shifts are followed by price drops — the market may already have priced it in.
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

          {isPro && (
            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">API ACCESS</span>
                <a
                  href="https://developers.sentimentfx.org"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--accent2)", letterSpacing: "0.08em", textDecoration: "none" }}
                >
                  DOCS →
                </a>
              </div>
              <div className="panel-body">
                {apiKeyFull ? (
                  <div>
                    <div className="api-warn-box">
                      ⚠ Save your API key now — it will not be shown again.
                    </div>
                    <div className="api-key-display">
                      <span style={{ wordBreak: "break-all" }}>{apiKeyFull}</span>
                      <button className="api-copy-btn" onClick={copyApiKey}>
                        {apiKeyCopied ? "COPIED" : "COPY"}
                      </button>
                    </div>
                    <button
                      onClick={() => setApiKeyFull(null)}
                      style={{
                        fontFamily: "var(--mono)", fontSize: "10px", padding: "6px 14px",
                        background: "var(--surface2)", border: "1px solid var(--border2)",
                        borderRadius: "2px", color: "var(--muted)", cursor: "pointer", letterSpacing: "0.08em"
                      }}
                    >
                      I've saved it
                    </button>
                  </div>
                ) : apiKeyInfo?.has_key ? (
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", flexWrap: "wrap" }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.15em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
                          API KEY
                        </div>
                        <div className="api-key-masked">
                          <span style={{ fontFamily: "var(--mono)", fontSize: "12px", color: "var(--accent)" }}>{apiKeyInfo.prefix}</span>
                          <span style={{ fontFamily: "var(--mono)", fontSize: "12px", color: "var(--muted)" }}>••••••••••••••••••••••••••••</span>
                        </div>
                        <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)", marginTop: "4px", letterSpacing: "0.05em" }}>
                          Pass as <code style={{ color: "var(--accent2)" }}>X-API-Key</code> header
                        </div>
                      </div>
                      <div style={{ flex: 1, minWidth: "160px" }}>
                        <div style={{ fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.15em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "6px" }}>
                          THIS MONTH
                        </div>
                        <div style={{ fontFamily: "var(--mono)", fontSize: "18px", fontWeight: 600, color: "var(--text)" }}>
                          {apiKeyInfo.calls_this_month ?? 0}
                          <span style={{ fontSize: "11px", fontWeight: 400, color: "var(--muted)", marginLeft: "4px" }}>
                            / {apiKeyInfo.total_monthly ?? (apiKeyInfo.free_calls + apiKeyInfo.monthly_allowance)} calls
                          </span>
                        </div>
                        <div className="api-usage-bar">
                          <div
                            className="api-usage-bar-fill"
                            style={{
                              width: `${Math.min(100, ((apiKeyInfo.calls_this_month ?? 0) / Math.max(1, apiKeyInfo.total_monthly ?? 1)) * 100)}%`
                            }}
                          />
                        </div>
                        <div style={{ fontFamily: "var(--mono)", fontSize: "9px", color: "var(--muted)" }}>
                          {apiKeyInfo.monthly_allowance > 0 ? `${apiKeyInfo.free_calls} free + ${apiKeyInfo.monthly_allowance} plan` : `${apiKeyInfo.free_calls} free calls`} · resets monthly
                        </div>
                      </div>
                    </div>
                    <div style={{ marginTop: "14px" }}>
                      <button
                        onClick={regenerateApiKey}
                        disabled={apiKeyLoading}
                        style={{
                          fontFamily: "var(--mono)", fontSize: "10px", padding: "5px 12px",
                          background: "transparent", border: "1px solid var(--negative)",
                          borderRadius: "2px", color: "var(--negative)", cursor: "pointer",
                          letterSpacing: "0.08em", opacity: apiKeyLoading ? 0.5 : 1
                        }}
                      >
                        {apiKeyLoading ? "..." : "REGENERATE KEY"}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <p style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)", marginBottom: "14px", letterSpacing: "0.04em", lineHeight: "1.6" }}>
                      Generate an API key to access sentiment, price, and correlation data programmatically.
                      Your {isData ? "5,000" : "1,000"} monthly calls are included with your plan.
                    </p>
                    <button
                      onClick={generateApiKey}
                      disabled={apiKeyLoading}
                      style={{
                        fontFamily: "var(--mono)", fontSize: "10px", fontWeight: 600,
                        letterSpacing: "0.1em", padding: "8px 18px", background: "var(--accent2)",
                        border: "none", borderRadius: "2px", color: "#080c10",
                        cursor: "pointer", textTransform: "uppercase", opacity: apiKeyLoading ? 0.5 : 1
                      }}
                    >
                      {apiKeyLoading ? "GENERATING..." : "GENERATE API KEY"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

        </main>
      </div>

      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialMode={authMode} />}

      {showAccount && user && (
        <AccountModal
          user={user}
          session={session}
          profile={profile}
          onClose={() => setShowAccount(false)}
          onSignOut={() => { supabase.auth.signOut(); setShowAccount(false) }}
          onProfileUpdate={(updated) => setProfile(updated)}
        />
      )}

      {showPasswordReset && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(8,12,16,0.85)",
          backdropFilter: "blur(4px)", zIndex: 200,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div style={{
            background: "#0d1117", border: "1px solid #21262d", borderRadius: "4px",
            padding: "40px", width: "100%", maxWidth: "400px",
          }}>
            <div style={{ fontFamily: "IBM Plex Mono", fontSize: "13px", fontWeight: 600, letterSpacing: "0.2em", color: "#f0b429", textTransform: "uppercase", marginBottom: "8px" }}>
              SentimentFX
            </div>
            <div style={{ fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#7d8590", letterSpacing: "0.1em", marginBottom: "32px" }}>
              SET NEW PASSWORD
            </div>
            {passwordResetDone ? (
              <div style={{ fontFamily: "IBM Plex Mono", fontSize: "11px", color: "#3fb950" }}>
                Password updated. You are now signed in.
              </div>
            ) : (
              <>
                <input
                  style={{
                    width: "100%", background: "#161b22", border: "1px solid #30363d",
                    borderRadius: "2px", padding: "10px 14px", fontFamily: "IBM Plex Mono",
                    fontSize: "12px", color: "#e6edf3", outline: "none", marginBottom: "12px",
                    boxSizing: "border-box",
                  }}
                  type="password"
                  placeholder="new password"
                  value={newPassword}
                  onChange={e => setNewPassword(e.target.value)}
                  onKeyDown={async e => {
                    if (e.key === "Enter") {
                      setPasswordResetLoading(true)
                      await supabase.auth.updateUser({ password: newPassword })
                      setPasswordResetDone(true)
                      setPasswordResetLoading(false)
                      setTimeout(() => setShowPasswordReset(false), 2000)
                    }
                  }}
                />
                <button
                  style={{
                    width: "100%", background: "#f0b429", border: "none", borderRadius: "2px",
                    padding: "12px", fontFamily: "IBM Plex Mono", fontSize: "11px",
                    fontWeight: 600, letterSpacing: "0.1em", color: "#080c10",
                    cursor: "pointer", textTransform: "uppercase",
                  }}
                  disabled={passwordResetLoading}
                  onClick={async () => {
                    setPasswordResetLoading(true)
                    await supabase.auth.updateUser({ password: newPassword })
                    setPasswordResetDone(true)
                    setPasswordResetLoading(false)
                    setTimeout(() => setShowPasswordReset(false), 2000)
                  }}
                >
                  {passwordResetLoading ? "..." : "Update Password"}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}