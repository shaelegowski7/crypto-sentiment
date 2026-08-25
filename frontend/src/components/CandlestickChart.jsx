import { useEffect, useRef, useState } from "react"
import { createChart, CandlestickSeries, HistogramSeries, ColorType, CrosshairMode } from "lightweight-charts"
import { _formatPrice } from "../lib/constants"

const UP_COLOR = "#42c768"    // --positive
const DOWN_COLOR = "#f26d64"  // --negative
const VOLUME_COLOR = "rgba(108, 178, 255, 0.55)"   // --accent2, up from the old 0.5/dark-blue mix which was hard to see
const SENT_POS = "rgba(66, 199, 104, 0.75)"
const SENT_NEG = "rgba(242, 109, 100, 0.75)"
const SENT_NEUTRAL = "rgba(139, 149, 167, 0.55)"

const _formatVolume = (v) => {
  if (v === null || v === undefined) return "—"
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return v.toFixed(0)
}

const _formatSent = (v) => (v === null || v === undefined ? "—" : Math.round(v))

// `candles` is already display-ready: [{ time, open, high, low, close, volume,
// sentiment }, ...] — OHLC pre-rated to the selected currency, sentiment
// pre-rescaled to 0-100 (or null where no headlines fell in that bucket) —
// see displayCandles in App.jsx. `interval` is "1h" | "4h" | "1d" — only used
// to toggle whether the time axis shows a time-of-day (intraday) or just a
// date (daily).
export default function CandlestickChart({ candles, interval, ticker, height = 420 }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const candleSeriesRef = useRef(null)
  const volumeSeriesRef = useRef(null)
  const sentimentSeriesRef = useRef(null)
  const [hovered, setHovered] = useState(null)

  // Created once; data/interval-specific tweaks happen in the effects below
  // via refs so the chart instance itself doesn't get torn down on every
  // ticker/interval switch.
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b95a7",
        fontFamily: "'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace",
        fontSize: 10,
        panes: { separatorColor: "#21262d" },
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: { borderColor: "#21262d" },
      timeScale: { borderColor: "#21262d", secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    })

    // Price: pane 0 (main). Volume and sentiment each get their own pane
    // below it — a generic OHLCV chart would stop at volume; the sentiment
    // pane is SentimentFX's own addition, on the same 0-100 scale (base: 50
    // so bars diverge up/down from neutral) as the rest of the dashboard.
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      borderVisible: false,
      wickUpColor: UP_COLOR,
      wickDownColor: DOWN_COLOR,
    }, 0)

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      color: VOLUME_COLOR,
    }, 1)

    const sentimentSeries = chart.addSeries(HistogramSeries, {
      base: 50,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
      color: SENT_NEUTRAL,
    }, 2)
    sentimentSeries.priceScale().applyOptions({ autoScale: false })
    sentimentSeries.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) })

    const panes = chart.panes()
    panes[0]?.setStretchFactor(4)
    panes[1]?.setStretchFactor(1.5)
    panes[2]?.setStretchFactor(1.5)

    chart.subscribeCrosshairMove((param) => {
      const bar = param.seriesData?.get(candleSeries)
      const vol = param.seriesData?.get(volumeSeries)
      const sent = param.seriesData?.get(sentimentSeries)
      setHovered(bar ? { ...bar, volume: vol?.value, sentiment: sent?.value } : null)
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries
    sentimentSeriesRef.current = sentimentSeries

    return () => {
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
      sentimentSeriesRef.current = null
    }
  }, [])

  // Daily candles show just a date on the time axis; intraday (1h/4h) shows
  // time-of-day too — otherwise every 1h/4h bar for the same day looks like
  // a duplicate tick.
  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { timeVisible: interval !== "1d" } })
  }, [interval])

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !sentimentSeriesRef.current) return
    const sorted = [...candles].sort((a, b) => a.time - b.time)
    candleSeriesRef.current.setData(sorted.map(c => ({
      time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
    })))
    volumeSeriesRef.current.setData(sorted.map(c => ({
      time: c.time,
      value: c.volume ?? 0,
      color: c.close >= c.open ? "rgba(66, 199, 104, 0.45)" : "rgba(242, 109, 100, 0.45)",
    })))
    // Bars with no headline coverage in that bucket are skipped entirely
    // (a gap) rather than plotted as a fake neutral 50 — see the `sentiment`
    // field's null-means-no-data contract from GET /candles.
    sentimentSeriesRef.current.setData(
      sorted
        .filter(c => c.sentiment !== null && c.sentiment !== undefined)
        .map(c => ({
          time: c.time,
          value: c.sentiment,
          color: c.sentiment > 55 ? SENT_POS : c.sentiment < 45 ? SENT_NEG : SENT_NEUTRAL,
        }))
    )
    chartRef.current?.timeScale().fitContent()
    setHovered(null)
  }, [candles])

  const last = hovered ?? [...candles].sort((a, b) => a.time - b.time).slice(-1)[0]

  return (
    <div style={{ position: "relative" }}>
      {last && (
        <div className="custom-tooltip" style={{
          position: "absolute", top: 0, left: 0, zIndex: 2,
          display: "flex", gap: "14px", padding: "6px 10px",
          background: "transparent", border: "none",
        }}>
          <div className="tooltip-row"><span className="tooltip-key">O</span><span className="tooltip-val">{_formatPrice(last.open, ticker)}</span></div>
          <div className="tooltip-row"><span className="tooltip-key">H</span><span className="tooltip-val">{_formatPrice(last.high, ticker)}</span></div>
          <div className="tooltip-row"><span className="tooltip-key">L</span><span className="tooltip-val">{_formatPrice(last.low, ticker)}</span></div>
          <div className="tooltip-row">
            <span className="tooltip-key">C</span>
            <span className="tooltip-val" style={{ color: last.close >= last.open ? "var(--positive)" : "var(--negative)" }}>
              {_formatPrice(last.close, ticker)}
            </span>
          </div>
          <div className="tooltip-row"><span className="tooltip-key">VOL</span><span className="tooltip-val">{_formatVolume(last.volume)}</span></div>
          <div className="tooltip-row">
            <span className="tooltip-key">SENT</span>
            <span className="tooltip-val" style={{ color: last.sentiment > 55 ? "var(--positive)" : last.sentiment < 45 ? "var(--negative)" : "var(--muted)" }}>
              {_formatSent(last.sentiment)}
            </span>
          </div>
        </div>
      )}
      <div ref={containerRef} style={{ width: "100%", height }} />
    </div>
  )
}
