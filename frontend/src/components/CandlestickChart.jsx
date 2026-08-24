import { useEffect, useRef, useState } from "react"
import { createChart, CandlestickSeries, HistogramSeries, ColorType, CrosshairMode } from "lightweight-charts"
import { _formatPrice } from "../lib/constants"

const UP_COLOR = "#42c768"    // --positive
const DOWN_COLOR = "#f26d64"  // --negative

const _formatVolume = (v) => {
  if (v === null || v === undefined) return "—"
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return v.toFixed(0)
}

// `candles` is the raw API shape: [{ ts: "2026-07-29T14:00:00", open, high, low, close, volume }, ...]
// `interval` is "1h" | "4h" | "1d" — only used to toggle whether the time axis
// shows a time-of-day (intraday) or just a date (daily).
export default function CandlestickChart({ candles, interval, ticker, height = 320 }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const candleSeriesRef = useRef(null)
  const volumeSeriesRef = useRef(null)
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
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: { borderColor: "#21262d" },
      timeScale: { borderColor: "#21262d", secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      borderVisible: false,
      wickUpColor: UP_COLOR,
      wickDownColor: DOWN_COLOR,
    })
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.25 } })

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    })
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

    chart.subscribeCrosshairMove((param) => {
      const bar = param.seriesData?.get(candleSeries)
      const vol = param.seriesData?.get(volumeSeries)
      setHovered(bar ? { ...bar, volume: vol?.value } : null)
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries

    return () => {
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
    }
  }, [])

  // Daily candles show just a date on the time axis; intraday (1h/4h) shows
  // time-of-day too — otherwise every 1h/4h bar for the same day looks like
  // a duplicate tick.
  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { timeVisible: interval !== "1d" } })
  }, [interval])

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return
    const sorted = [...candles].sort((a, b) => a.time - b.time)
    candleSeriesRef.current.setData(sorted.map(c => ({
      time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
    })))
    volumeSeriesRef.current.setData(sorted.map(c => ({
      time: c.time,
      value: c.volume ?? 0,
      color: c.close >= c.open ? "rgba(66, 199, 104, 0.5)" : "rgba(242, 109, 100, 0.5)",
    })))
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
        </div>
      )}
      <div ref={containerRef} style={{ width: "100%", height }} />
    </div>
  )
}
