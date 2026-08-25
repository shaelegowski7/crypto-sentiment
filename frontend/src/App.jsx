import { useState, useEffect, lazy, Suspense } from "react"
import axios from "axios"
import { supabase } from "./supabaseClient"
import AuthModal from "./AuthModal"
import AccountModal from "./AccountModal"
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from "recharts"
import "./dashboard.css"
import { API, FX_LABELS, COMMODITY_LABELS, TICKER_SLUGS, FX_TICKERS, TICKER_INFO, nativeCurrencyFor, _formatPrice, _formatSentiment, toSentimentScale, toSentimentDelta, fromSentimentScale, redirectToCheckout } from "./lib/constants"
import CandlestickChart from "./components/CandlestickChart"
import SentimentGauge from "./components/SentimentGauge"

// Standalone pages are code-split — they only load on their own routes.
const Leaderboard = lazy(() => import("./pages/Leaderboard"))
const TrackRecord = lazy(() => import("./pages/TrackRecord"))
const Brief = lazy(() => import("./pages/Brief"))

const CATEGORIES = {
  Crypto: ["BTC", "ETH", "SOL", "XRP", "DOGE"],
  FX: ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"],
  Stocks: ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "BAC", "GS", "V", "MA", "XOM", "JNJ", "AMD", "NFLX", "WMT", "UBER", "CRM", "PLTR"],
  ETFs: ["SPY", "QQQ", "GLD", "SLV", "USO", "ARKK"],
  Commodities: ["GC=F", "SI=F", "CL=F", "NG=F"],
}
const TICKERS = Object.values(CATEGORIES).flat()
// Top 3 by total headline count in each category (queried 2026-07-04) — the
// best-covered tickers make the strongest free-tier first impression. Not
// auto-computed; re-derive periodically as coverage shifts (GROUP BY ticker
// on the headlines table, ranked within each CATEGORIES bucket).
const FREE_TICKERS = [
  "BTC", "ETH", "SOL",               // Crypto
  "EURUSD", "USDJPY", "AUDUSD",      // FX
  "GOOGL", "AAPL", "NVDA",           // Stocks
  "SPY", "QQQ", "USO",               // ETFs
  "CL=F", "GC=F", "NG=F",            // Commodities
]
// Mirror of landing/sentiment-tickers.json — kept in sync manually because the
// frontend bundles independently of the landing repo.  Used by the leaderboard
// row links to deep-link into the per-ticker SEO landing page on sentimentfx.org.
const HEADLINES_PER_PAGE = 10

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
  let narrative = `${ticker} sentiment is currently ${sentimentLabel} (${toSentimentScale(score)}/100).`

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
    const deltaDisplay = toSentimentDelta(trend.delta)
    narrative += ` Sentiment has been ${trendWord} over the past week (${deltaDisplay > 0 ? "+" : ""}${deltaDisplay}).`
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
              {toSentimentDelta(sentiment_change_7d) > 0 ? "+" : ""}{toSentimentDelta(sentiment_change_7d)}
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

  const scaledDelta = delta !== null ? toSentimentDelta(delta) : null
  const deltaDisplay = scaledDelta !== null
     ? `${scaledDelta > 0 ? "+" : ""}${scaledDelta}`
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
      <div className="signal-card signal-neutral">
        <div className="skeleton" style={{ height: "12px", width: "120px" }} />
        <div className="skeleton" style={{ height: "28px", width: "200px" }} />
        <div className="skeleton" style={{ height: "40px", width: "90%" }} />
      </div>
    )
  }

  if (!signal) return null

  const tone = {
    BULLISH: "signal-bullish",
    BEARISH: "signal-bearish",
    NEUTRAL: "signal-neutral",
  }[signal.direction] ?? "signal-neutral"

  return (
    <div className={`signal-card ${tone}`}>
      {/* Header row */}
      <div className="signal-header">
        <div className="signal-header-left">
          <span className="signal-eyebrow">Today's Signal</span>
          <span className="signal-badge">{signal.direction}</span>
          {signal.isMomentum !== null && signal.strength !== "inconclusive" && (
            <span className="signal-chip-wrap">
              <span className="signal-chip">
                {signal.isMomentum ? "MOMENTUM" : "CONTRARIAN"}
              </span>
              <InfoTip text={signal.isMomentum
                ? "Positive sentiment shifts have historically preceded price rises for this ticker."
                : "Positive sentiment shifts have historically preceded price drops — the market may have already priced the news in."} />
            </span>
          )}
          {signal.beatsMomentum === true && signal.strength !== "inconclusive" && (
            <span className="signal-chip-wrap">
              <span className="signal-chip signal-chip-positive">BEATS BASELINE</span>
              <InfoTip text="The sentiment signal has historically outperformed a simple price momentum strategy over the last 180 days." />
            </span>
          )}
        </div>
        {signal.strength && <StrengthMeter strength={signal.strength} />}
      </div>

      {/* Narrative */}
      <p className="signal-narrative">{signal.narrative}</p>

      {/* Bottom row: score + trend + correlation */}
      <div className="signal-metrics">
        <div className="signal-metric">
          <div className="signal-metric-label">
            Sentiment Score
            <InfoTip text="Average FinBERT score across today's headlines. Ranges from 0 (very negative) to 100 (very positive), 50 is neutral. Scored using a financial-domain AI model." />
          </div>
          <SentimentGauge score={toSentimentScale(signal.score)} />
        </div>

        <div className="signal-divider" />

        <div className="signal-metric">
          <div className="signal-metric-label">
            7-Day Trend
            <InfoTip text="Average sentiment over the last 7 days vs the 7 days before that. Shows whether the overall news tone is improving or deteriorating." />
          </div>
          <TrendArrow trend={trend} />
        </div>

        {signal.correlation !== null && (
          <>
            <div className="signal-divider" />
            <div className="signal-metric">
              <div className="signal-metric-label">
                Predictive Link
                <InfoTip text="How reliably sentiment shifts have led next-day price moves over the last 180 days, measured as a Pearson correlation (r). r runs -1 to +1 — positive means price tends to follow sentiment, negative means it moves opposite, and near 0 means no relationship. Strength accounts for both effect size and statistical significance." />
              </div>
              {/* Lead with the plain-English verdict; r stays on its standard
                  -1..+1 scale underneath (deliberately NOT rescaled to 0-100
                  like sentiment — the sign carries the meaning, and the CI /
                  p-value alongside it are defined on that same scale). */}
              <div className="signal-metric-value signal-strength-value" style={{
                color: signal.strength === "strong" ? "var(--positive)"
                  : signal.strength === "weak" ? "var(--accent)"
                  : "var(--muted)",
              }}>
                {(signal.strength ?? "unknown").toUpperCase()}
              </div>
              <div className="signal-metric-sub">
                r = {signal.correlation > 0 ? "+" : ""}{signal.correlation} · n={signal.sampleSize ?? "?"}
              </div>
            </div>
          </>
        )}

        {signal.shiftPercentile !== undefined && signal.shiftPercentile !== null && (
          <>
            <div className="signal-divider" />
            <div className="signal-metric">
              <div className="signal-metric-label">
                Shift Percentile
                <InfoTip text="How large today's sentiment shift is compared to all historical daily shifts. 90th percentile = larger move than 90% of recorded days. Higher = rarer, potentially more significant." />
              </div>
              <div className={`signal-metric-value ${signal.shiftPercentile >= 75 ? "accent-text" : "neutral-text"}`}>
                {signal.shiftPercentile}th
              </div>
              <div className="signal-metric-sub">{signal.shiftMagnitude} · {signal.articleCount} articles</div>
            </div>
          </>
        )}

      </div>
    </div>
  )
}
      

// All dashboard styling lives in dashboard.css (imported at the top of this
// file). `styles` is kept as an empty string so the legacy <style>{styles}</style>
// injection points in Dashboard/Leaderboard/TrackRecord stay valid.
const styles = ""

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
                    title={`${day.date}${day.sentiment !== null ? ` · ${toSentimentScale(day.sentiment)}/100` : " · no data"}`}
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
              {toSentimentScale(selectedScore)}
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
                  {toSentimentScale(h.score)}
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
                        {toSentimentScale(h.sentiment_score)}
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
  const [btDirection, setBtDirection] = useState("momentum")
  const [btCostsMode, setBtCostsMode] = useState("default")
  const [btCostsCustom, setBtCostsCustom] = useState(30)
  const [btView, setBtView] = useState("net")
  const [btAdvancedOpen, setBtAdvancedOpen] = useState(false)
  const [btStopLoss, setBtStopLoss] = useState("")
  const [btTakeProfit, setBtTakeProfit] = useState("")
  const [btSize, setBtSize] = useState(100)
  const [btThresholdS, setBtThresholdS] = useState("")
  const [btThresholdP, setBtThresholdP] = useState("")
  const [btShiftThresh, setBtShiftThresh] = useState("")

  useEffect(() => {
    let cancelled = false
    setBtLoading(true)
    setBtData(null)
    const params = new URLSearchParams({
      signal: btSignal,
      hold_days: String(btHoldDays),
      direction_mode: btDirection,
      size_pct: String(btSize),
    })
    if (btCostsMode === "zero") params.set("costs_bps", "0")
    else if (btCostsMode === "custom") params.set("costs_bps", String(btCostsCustom))
    if (btStopLoss !== "") params.set("stop_loss_pct", btStopLoss)
    if (btTakeProfit !== "") params.set("take_profit_pct", btTakeProfit)
    if (btThresholdS !== "") params.set("threshold_s", btThresholdS)
    if (btThresholdP !== "") params.set("threshold_p", btThresholdP)
    if (btShiftThresh !== "") params.set("shift_thresh", btShiftThresh)
    axios.get(`${API}/backtest/${ticker}?${params.toString()}`)
      .then(r => { if (!cancelled) { setBtData(r.data); setBtLoading(false) } })
      .catch(() => { if (!cancelled) { setBtData({ error: true }); setBtLoading(false) } })
    return () => { cancelled = true }
  }, [ticker, btSignal, btHoldDays, btDirection, btCostsMode, btCostsCustom, btStopLoss, btTakeProfit, btSize, btThresholdS, btThresholdP, btShiftThresh])

  const summary = btData?.summary?.[btView]
  const costsPctPerTrade = btData?.summary?.costs_pct_per_trade
  const trades = btData?.trades ?? []
  const byRegime = btData?.by_regime
  const walkForward = btData?.walk_forward
  const btNativeCurrency = nativeCurrencyFor(ticker)
  const priceUnitLabel = btNativeCurrency === "GBP" ? " (£)" : btNativeCurrency === "USD" ? " ($)" : ""
  const equityCurve = btData?.equity_curve ?? []

  const retColor = (v) => v > 0 ? "var(--positive)" : v < 0 ? "var(--negative)" : "var(--muted)"
  const ctrlLabelStyle = { fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)", letterSpacing: "0.08em" }
  const sectionLabelStyle = { fontFamily: "var(--mono)", fontSize: "9px", letterSpacing: "0.1em", color: "var(--muted)", textTransform: "uppercase", marginBottom: "8px" }
  const numInputStyle = { width: "60px" }

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
            <span style={ctrlLabelStyle}>SIGNAL</span>
            {["divergence", "shift"].map(s => (
              <button key={s} className={`seg-btn ${btSignal === s ? "active" : ""}`} onClick={() => setBtSignal(s)}>
                {s === "divergence" ? "DIVERGENCE" : "SHIFT"}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={ctrlLabelStyle}>HOLD</span>
            {[1, 3, 7, 14].map(h => (
              <button key={h} className={`seg-btn ${btHoldDays === h ? "active" : ""}`} onClick={() => setBtHoldDays(h)}>{h}D</button>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={ctrlLabelStyle}>DIRECTION</span>
            {["momentum", "contrarian"].map(m => (
              <button key={m} className={`seg-btn ${btDirection === m ? "active" : ""}`} onClick={() => setBtDirection(m)}>{m.toUpperCase()}</button>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={ctrlLabelStyle}>COSTS</span>
            {[["default", "DEFAULT"], ["zero", "0BPS"], ["custom", "CUSTOM"]].map(([v, label]) => (
              <button key={v} className={`seg-btn ${btCostsMode === v ? "active" : ""}`} onClick={() => setBtCostsMode(v)}>{label}</button>
            ))}
            {btCostsMode === "custom" && (
              <input
                className="alert-input" style={numInputStyle} type="number" min="0" max="500"
                value={btCostsCustom} onChange={e => setBtCostsCustom(e.target.value)}
              />
            )}
          </div>
        </div>

        {/* Advanced: stop-loss / take-profit / position size / signal thresholds.
            All optional — empty means "use the default", matching production. */}
        <div>
          <button
            onClick={() => setBtAdvancedOpen(o => !o)}
            style={{ ...ctrlLabelStyle, background: "none", border: "none", cursor: "pointer", padding: 0 }}
          >
            {btAdvancedOpen ? "▾" : "▸"} ADVANCED
          </button>
          {btAdvancedOpen && (
            <div style={{ display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap", marginTop: "10px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={ctrlLabelStyle}>SIZE %</span>
                <input className="alert-input" style={numInputStyle} type="number" min="1" max="100"
                  value={btSize} onChange={e => setBtSize(e.target.value)} />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={ctrlLabelStyle}>STOP LOSS %</span>
                <input className="alert-input" style={numInputStyle} type="number" min="0" max="90" placeholder="off"
                  value={btStopLoss} onChange={e => setBtStopLoss(e.target.value)} />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={ctrlLabelStyle}>TAKE PROFIT %</span>
                <input className="alert-input" style={numInputStyle} type="number" min="0" max="500" placeholder="off"
                  value={btTakeProfit} onChange={e => setBtTakeProfit(e.target.value)} />
              </div>
              {btSignal === "divergence" ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={ctrlLabelStyle}>SENTIMENT THRESH</span>
                    <input className="alert-input" style={numInputStyle} type="number" step="0.01" min="0.001" max="1" placeholder="0.02"
                      value={btThresholdS} onChange={e => setBtThresholdS(e.target.value)} />
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={ctrlLabelStyle}>PRICE THRESH %</span>
                    <input className="alert-input" style={numInputStyle} type="number" step="0.1" min="0.01" max="50" placeholder="0.5"
                      value={btThresholdP} onChange={e => setBtThresholdP(e.target.value)} />
                  </div>
                </>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <span style={ctrlLabelStyle}>SHIFT THRESH</span>
                  <input className="alert-input" style={numInputStyle} type="number" step="0.01" min="0.001" max="1" placeholder="0.05"
                    value={btShiftThresh} onChange={e => setBtShiftThresh(e.target.value)} />
                </div>
              )}
            </div>
          )}
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
            {/* Gross/net view toggle */}
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <span style={ctrlLabelStyle}>VIEW</span>
              {["net", "gross"].map(v => (
                <button key={v} className={`seg-btn ${btView === v ? "active-blue" : ""}`} onClick={() => setBtView(v)}>{v.toUpperCase()}</button>
              ))}
              {costsPctPerTrade != null && (
                <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--muted)" }}>
                  {btView === "net" ? `net of ${costsPctPerTrade.toFixed(2)}% cost/trade` : "before transaction costs"}
                </span>
              )}
            </div>

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

            {/* Regime breakdown */}
            {byRegime && (
              <div>
                <div style={sectionLabelStyle}>BY MARKET REGIME</div>
                <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
                  {["bull", "bear", "chop", "unknown"].filter(r => byRegime[r]).map(r => (
                    <div key={r} className="stat-card" style={{ minWidth: "130px", flex: "1 1 130px" }}>
                      <div className="stat-label">{r.toUpperCase()}</div>
                      <div className="stat-value" style={{ color: retColor(byRegime[r].total_return_pct) }}>
                        {byRegime[r].total_return_pct > 0 ? "+" : ""}{byRegime[r].total_return_pct}%
                      </div>
                      <div className="stat-sub">{byRegime[r].trades} trades · {(byRegime[r].win_rate * 100).toFixed(0)}% win</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Walk-forward stability */}
            {walkForward?.stability && (
              <div>
                <div style={sectionLabelStyle}>WALK-FORWARD STABILITY</div>
                <div className="stat-row">
                  <div className="stat-card">
                    <div className="stat-label">Positive Folds</div>
                    <div className="stat-value" style={{ color: walkForward.stability.pct_folds_positive >= 0.7 ? "var(--positive)" : "var(--negative)" }}>
                      {walkForward.stability.folds_positive}/{walkForward.stability.folds_with_trades}
                    </div>
                    <div className="stat-sub">{(walkForward.stability.pct_folds_positive * 100).toFixed(0)}% of folds</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Mean Fold Return</div>
                    <div className="stat-value" style={{ color: retColor(walkForward.stability.mean_net_return_pct) }}>
                      {walkForward.stability.mean_net_return_pct > 0 ? "+" : ""}{walkForward.stability.mean_net_return_pct}%
                    </div>
                    <div className="stat-sub">±{walkForward.stability.std_net_return_pct}% std</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Best / Worst Fold</div>
                    <div className="stat-value" style={{ color: "var(--text)" }}>
                      {walkForward.stability.best_fold_pct}% / {walkForward.stability.worst_fold_pct}%
                    </div>
                    <div className="stat-sub">{walkForward.stability.folds_total} folds, {walkForward.stability.fold_window_days}d window</div>
                  </div>
                </div>
              </div>
            )}

            {/* Trades table */}
            {trades.length > 0 && (
              <div>
                <div style={sectionLabelStyle}>
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
                      <span>ENTRY</span><span>EXIT</span><span>IN{priceUnitLabel}</span><span>OUT{priceUnitLabel}</span><span style={{ textAlign: "right" }}>RETURN</span>
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
                          <span style={{ color: "var(--muted)" }}>
                            {BT_DATE_FMT(t.exit_date)}
                            {t.exit_reason && t.exit_reason !== "hold_days" && (
                              <span style={{ color: "var(--accent)" }}> ({t.exit_reason === "stop_loss" ? "SL" : "TP"})</span>
                            )}
                          </span>
                          <span style={{ color: "var(--text)" }}>{_formatPrice(t.entry_price, ticker)}</span>
                          <span style={{ color: "var(--text)" }}>{_formatPrice(t.exit_price, ticker)}</span>
                          <span style={{ color: retColor(t.return_pct), textAlign: "right", fontWeight: 600 }}>
                            {t.return_pct != null ? `${t.return_pct > 0 ? "+" : ""}${t.return_pct}%` : "—"}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div className="disclaimer">
              ⚠ {btDirection === "contrarian" ? "Contrarian (buy-the-panic)" : "Long-only momentum"} strategy. Entry at next close after signal
              {(btStopLoss || btTakeProfit) ? ", exits early on stop-loss/take-profit or " : ", exit "}
              after {btHoldDays} calendar days. Past results do not predict future performance. Not financial advice.
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


// Top-level dispatcher.  Keeps the hooks rule clean: the dashboard hooks only
// run when we're actually rendering Dashboard, and Leaderboard's hooks only
// run when we're on /leaderboard — no conditional hook calls inside either.
export default function App() {
  const pathname = typeof window !== "undefined" ? window.location.pathname : "/"
  if (pathname === "/leaderboard" || pathname === "/track-record" || pathname === "/brief" || pathname.startsWith("/brief/")) {
    const Page = pathname === "/leaderboard" ? Leaderboard : pathname === "/track-record" ? TrackRecord : Brief
    return (
      <Suspense fallback={<div className="dashboard" style={{ minHeight: "100vh" }} />}>
        <Page />
      </Suspense>
    )
  }
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
  // 0-100 scale (display) — converted back to the API's native -1..1 scale
  // at submission time in createAlert(). 65 === toSentimentScale(0.3).
  const [alertThreshold, setAlertThreshold] = useState(65)
  const [alertDirection, setAlertDirection] = useState("above")
  const [alertLoading, setAlertLoading] = useState(false)
  const [signalData, setSignalData] = useState(null)
  const [divergenceData, setDivergenceData] = useState(null)
  const [apiKeyInfo, setApiKeyInfo] = useState(null)
  const [apiKeyFull, setApiKeyFull] = useState(null)
  const [apiKeyLoading, setApiKeyLoading] = useState(false)
  const [apiKeyCopied, setApiKeyCopied] = useState(false)
  // Headline feed defaults to editorial news — Reddit/StockTwits chatter is
  // ~75% of raw volume and reads as noise in the feed. Sentiment scoring is
  // unaffected either way; this is display-only (see /dashboard `sources`).
  const [headlineSources, setHeadlineSources] = useState("news")
  const [chartMode, setChartMode] = useState("line")
  const [candleInterval, setCandleInterval] = useState("1h")
  const [candleData, setCandleData] = useState([])
  const [candleLoading, setCandleLoading] = useState(false)

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
  }, [ticker, headlineSources])

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
          threshold: fromSentimentScale(alertThreshold),
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
        ? `${baseUrl}&page=1&limit=50&sources=${headlineSources}`
        : `${baseUrl}&page=${headlinePageNum}&limit=50&sources=${headlineSources}`

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

  // Lazy-loaded: only fetched once the user switches to Candles mode, and
  // re-fetched on ticker/interval change while in that mode. Independent of
  // fetchDashboard's line-chart data — different endpoint, different shape.
  useEffect(() => {
    if (chartMode !== "candles") return
    let cancelled = false
    setCandleLoading(true)
    axios.get(`${API}/candles/${ticker}?interval=${candleInterval}&limit=500`)
      .then(r => { if (!cancelled) { setCandleData(r.data.candles ?? []); setCandleLoading(false) } })
      .catch(() => { if (!cancelled) { setCandleData([]); setCandleLoading(false) } })
    return () => { cancelled = true }
  }, [chartMode, ticker, candleInterval])

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

  // Prices are stored in the ticker's *native* currency — GBP for crypto,
  // raw rate for FX, USD for everything else (stocks/ETFs/commodities;
  // the backend never converts these — see nativeCurrencyFor()). The GBP/USD
  // toggle converts FROM that native currency, not always from GBP.
  const nativeCurrency = nativeCurrencyFor(ticker)
  const rate = isFX || currency === nativeCurrency || !gbpToUsd
    ? 1
    : nativeCurrency === "GBP" ? gbpToUsd : 1 / gbpToUsd
  const symbol = isFX ? "" : (currency === "USD" ? "$" : "£")

  const displayData = filteredData.map(d => ({
    ...d,
    price: d.price != null ? parseFloat((d.price * rate).toFixed(2)) : null,
    sentiment: toSentimentScale(d.sentiment),
  }))

  // Same `rate` conversion the line chart applies, plus the naive-UTC ->
  // epoch-seconds parsing this codebase already uses for backend timestamps
  // elsewhere (see the "+ 'Z'" pattern on h.published_at above).
  const displayCandles = candleData.map(c => ({
    time: Math.floor(new Date(c.ts + "Z").getTime() / 1000),
    open: c.open * rate,
    high: c.high * rate,
    low: c.low * rate,
    close: c.close * rate,
    volume: c.volume,
    sentiment: toSentimentScale(c.sentiment),
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
            <a href="https://sentimentfx.org" className="logo">SentimentFX</a>
            <div className="logo-divider" />
            <span className="tagline">CRYPTO SENTIMENT INTELLIGENCE</span>
          </div>
          <div className="topbar-right">
            <a
              href="/leaderboard"
              className="topbar-link"
            >
              LEADERBOARD
            </a>
            <a
              href="/brief"
              className="topbar-link"
            >
              BRIEF
            </a>
            <a
              href="https://developers.sentimentfx.org"
              target="_blank"
              rel="noreferrer"
              className="topbar-link"
            >
              DEVELOPERS
            </a>
            {user ? (
              <>
                <span className={tierBadgeClass}>{profile?.tier ?? "free"}</span>
                <button
                  onClick={() => setShowAccount(true)}
                  className="topbar-btn"
                >
                  ACCOUNT
                </button>
              </>
            ) : (
              <button
                onClick={() => { setAuthMode("login"); setShowAuth(true) }}
                className="topbar-btn-accent"
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
                title={TICKER_INFO[t] ? `${TICKER_INFO[t].name} — ${TICKER_INFO[t].blurb}` : undefined}
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
                <strong>Free tier:</strong> {FREE_TICKERS.length} top tickers across crypto, FX, stocks, ETFs and commodities · 30 day history · Sign in to unlock all 42 tickers, full history, API access and alerts.
              </span>
              <button className="upgrade-btn" onClick={() => { setAuthMode("signup"); setShowAuth(true) }}>
                Sign In / Sign Up
              </button>
            </div>
          )}

          {isLoggedIn && !isPro && (
            <div className="upgrade-banner">
              <span className="upgrade-text">
                <strong>Free tier:</strong> {FREE_TICKERS.length} top tickers · 30 day history · Upgrade to Pro for all 42 tickers across crypto, FX, stocks, ETFs and commodities, full history, 1,000 API calls/mo, alerts and morning brief.
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
              {TICKER_INFO[ticker] ? (
                <a
                  href={TICKER_INFO[ticker].url}
                  target="_blank"
                  rel="noreferrer"
                  className="stat-sub"
                  style={{ display: "block", color: "var(--accent2)", textDecoration: "none" }}
                  title={TICKER_INFO[ticker].blurb}
                >
                  {TICKER_INFO[ticker].name} ↗
                </a>
              ) : (
                <div className="stat-sub">Selected ticker</div>
              )}
            </div>
            <div className="stat-card">
              <div className="stat-label">Avg Sentiment</div>
              <div className={`stat-value ${!loading && avgSentiment > 0.1 ? "positive-text" : !loading && avgSentiment < -0.1 ? "negative-text" : "neutral-text"}`}>
                {loading
                  ? <span className="skeleton" style={{ display: "inline-block", width: "80px", height: "22px", borderRadius: "2px" }} />
                  : (avgSentiment !== null && avgSentiment !== undefined ? toSentimentScale(avgSentiment) : "—")}
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
                {isFX
                  ? "exchange rate"
                  : currency === nativeCurrency
                    ? (nativeCurrency === "GBP" ? "British pound (native)" : "US dollar (native)")
                    : nativeCurrency === "GBP"
                      ? `USD · rate: ${gbpToUsd?.toFixed(4) ?? "..."}`
                      : `GBP · rate: ${gbpToUsd ? (1 / gbpToUsd).toFixed(4) : "..."}`}
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
                        {sentimentTrend.delta !== null ? ` ${Math.abs(toSentimentDelta(sentimentTrend.delta))}` : ""}
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
                  className="seg-btn"
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
                {["line", "candles"].map(m => (
                  <button key={m} onClick={() => setChartMode(m)} className={`seg-btn ${chartMode === m ? "active" : ""}`}>
                    {m === "line" ? "LINE" : "CANDLES"}
                  </button>
                ))}
                <div className="control-divider" />
                {!isFX && ["GBP", "USD"].map(c => (
                  <button key={c} onClick={() => setCurrency(c)} className={`seg-btn ${currency === c ? "active-blue" : ""}`}>{c}</button>
                ))}
                <div className="control-divider" />
                {chartMode === "line" ? [7, 30, 90, 999].map(r => (
                  <button
                    key={r}
                    onClick={() => {
                      if (!isPro && r > 30) { setAuthMode("signup"); setShowAuth(true); return }
                      setRange(r)
                      fetchDashboard(r)
                    }}
                    className={`seg-btn ${range === r ? "active" : ""}${!isPro && r > 30 ? " locked" : ""}`}
                  >
                    {r === 999 ? "ALL" : `${r}D`}
                  </button>
                )) : ["1h", "4h", "1d"].map(iv => (
                  <button key={iv} onClick={() => setCandleInterval(iv)} className={`seg-btn ${candleInterval === iv ? "active" : ""}`}>
                    {iv.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
            {chartMode === "line" ? (
              loading ? <ChartSkeleton /> : (
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
                      domain={[0, 100]}
                      stroke="#30363d"
                      tick={{ fill: "#7d8590", fontSize: 9, fontFamily: "IBM Plex Mono" }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <Tooltip content={<CustomTooltip symbol={symbol} />} />
                    <Legend wrapperStyle={{ fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#7d8590", paddingTop: "12px" }} />
                    <Bar yAxisId="sentiment" dataKey="sentiment" name="Sentiment" fill="#f0b429" opacity={0.6} radius={[1, 1, 0, 0]} isAnimationActive={false} />
                    <Line yAxisId="price" type="monotone" dataKey="price" name="Price" stroke="#6cb2ff" dot={false} strokeWidth={1.5} connectNulls={false} isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              )
            ) : (
              candleLoading ? <ChartSkeleton /> : (
                <div className="panel-body">
                  {displayCandles.length > 0 ? (
                    <CandlestickChart candles={displayCandles} interval={candleInterval} ticker={ticker} height={480} />
                  ) : (
                    <div style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--muted)", padding: "24px 0", textAlign: "center" }}>
                      No candle data yet for this ticker/interval.
                    </div>
                  )}
                </div>
              )
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
              <div className="panel-controls">
                {[["news", "NEWS"], ["all", "ALL"]].map(([v, lbl]) => (
                  <button
                    key={v}
                    className={`seg-btn ${headlineSources === v ? "active" : ""}`}
                    onClick={() => { setHeadlineSources(v); setHeadlinePage(1) }}
                  >{lbl}</button>
                ))}
                <div className="control-divider" />
                <span className="panel-title" style={{ color: "#7d8590" }}>
                  {loading ? "—" : `${headlines.length} ITEMS · PG ${headlinePage}/${totalPages || 1}`}
                </span>
              </div>
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
                        {h.source_kind && h.source_kind !== "news" && (
                          <span className="source-badge">{h.source_kind.toUpperCase()}</span>
                        )}
                      </div>
                      <div className={`headline-score ${h.score > 0.1 ? "positive-text" : h.score < -0.1 ? "negative-text" : "neutral-text"}`}>
                        {toSentimentScale(h.score)}
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
                    Each news headline is scored from <span style={{ color: "var(--negative)" }}>0 (very negative)</span> to <span style={{ color: "var(--positive)" }}>100 (very positive)</span>, 50 is neutral, using an AI model trained on financial news. The average of all recent headlines gives the overall sentiment score.
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
                      min="0"
                      max="100"
                      step="1"
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
                              {toSentimentScale(a.threshold)}
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