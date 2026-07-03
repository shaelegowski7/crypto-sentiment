import { useState, useEffect } from "react"
import axios from "axios"
import { supabase } from "../supabaseClient"
import AuthModal from "../AuthModal"
import { API, FX_LABELS, COMMODITY_LABELS, TICKER_SLUGS, _formatPrice } from "../lib/constants"
import "../dashboard.css"

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

  // Regime breakdown — tags each trade by its entry-day trailing-60d trend
  // (bull / bear / chop, ±15% annualised). Shows net total return per bucket
  // so a strategy that's blended-positive but loses in bears can't hide.
  // Trade counts pop in the tooltip; the cell shows the headline pct.
  { key: "regime_bull", label: "Bull NR%", align: "right",
    hint: "Net total return on trades entered during bull regimes (trailing 60d >= +15% annualised). Hover for trade count.",
    val: (r) => {
      const b = _g("by_regime.bull")(r)
      if (!b) return "—"
      return _pct(b.total_return_pct)
    },
    sort: (r) => _g("by_regime.bull.total_return_pct")(r),
    color: (r) => _g("by_regime.bull.total_return_pct")(r) },
  { key: "regime_bear", label: "Bear NR%", align: "right",
    hint: "Net total return on trades entered during bear regimes (trailing 60d <= -15% annualised). Hover for trade count.",
    val: (r) => {
      const b = _g("by_regime.bear")(r)
      if (!b) return "—"
      return _pct(b.total_return_pct)
    },
    sort: (r) => _g("by_regime.bear.total_return_pct")(r),
    color: (r) => _g("by_regime.bear.total_return_pct")(r) },
  { key: "regime_chop", label: "Chop NR%", align: "right",
    hint: "Net total return on trades entered during chop regimes (trailing 60d between ±15% annualised). Hover for trade count.",
    val: (r) => {
      const b = _g("by_regime.chop")(r)
      if (!b) return "—"
      return _pct(b.total_return_pct)
    },
    sort: (r) => _g("by_regime.chop.total_return_pct")(r),
    color: (r) => _g("by_regime.chop.total_return_pct")(r) },

  // Walk-forward stability: how many sliding-window folds came back positive.
  // "7/10 σ12%" means 7 of 10 folds were net-positive; the standard deviation
  // of fold net returns was 12%. High σ relative to mean = regime-dependent.
  // A strategy with high OOS net but low pct positive folds is suspect.
  { key: "wf_stability", label: "WF +folds", align: "right",
    hint: "Walk-forward stability: positive folds / total folds (σ = standard deviation of fold net returns). Across all folds the SAME static thresholds run on a sliding window. >70% positive with low σ = the edge survives regime changes. Sortable by % positive.",
    val: (r) => {
      const s = _g("walk_forward.stability")(r)
      if (!s || !s.folds_with_trades) return "—"
      return `${s.folds_positive}/${s.folds_with_trades} σ${s.std_net_return_pct?.toFixed(0) ?? "?"}%`
    },
    sort: (r) => _g("walk_forward.stability.pct_folds_positive")(r),
    color: (r) => {
      // Colour the cell by % positive folds: >=70% green, <=30% red, else neutral.
      const pct = _g("walk_forward.stability.pct_folds_positive")(r)
      if (pct == null) return null
      if (pct >= 0.7) return 1
      if (pct <= 0.3) return -1
      return 0
    } },
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
        {" "}<strong style={{ color: "var(--text)" }}>Then check WF +folds and the regime split.</strong>
        {" "}WF runs the same static thresholds across overlapping
        {" "}{board?.walk_forward_params?.window_days ?? 45}-day windows (step
        {" "}{board?.walk_forward_params?.step_days ?? 15}d); ≥70% positive folds with low σ means the edge holds
        across regimes. Bull/Bear/Chop NR% bucket each trade by trailing-60d trend (±15% annualised) so you can see
        if the strategy makes its money in one regime and bleeds in another.
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
      <div className="dashboard">
        <header className="topbar">
          <div className="topbar-left">
            <a href="https://sentimentfx.org" className="logo">SentimentFX</a>
            <div className="logo-divider" />
            <span className="tagline">SENTIMENT LEADERBOARD</span>
          </div>
          <div className="topbar-right">
            <a href="/" className="topbar-link">DASHBOARD</a>
            <a href="/track-record" className="topbar-link">TRACK RECORD</a>
            <a href="https://developers.sentimentfx.org" target="_blank" rel="noreferrer" className="topbar-link">DEVELOPERS</a>
            {session ? (
              <a href="/" className="topbar-btn">{(session.user?.email ?? "ACCOUNT").slice(0, 18)} ↗</a>
            ) : (
              <>
                <button onClick={() => openAuth("login")} className="topbar-link">LOG IN</button>
                <button onClick={() => openAuth("signup")} className="topbar-btn-accent">SIGN UP</button>
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

export default Leaderboard
