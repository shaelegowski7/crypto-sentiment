import { useState, useEffect } from "react"
import axios from "axios"
import { supabase } from "../supabaseClient"
import AuthModal from "../AuthModal"
import { API, FX_LABELS, COMMODITY_LABELS, _formatPrice } from "../lib/constants"
import "../dashboard.css"

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
      <div className="dashboard">
        <header className="topbar">
          <div className="topbar-left">
            <a href="https://sentimentfx.org" className="logo">SentimentFX</a>
            <div className="logo-divider" />
            <span className="tagline">TRACK RECORD</span>
          </div>
          <div className="topbar-right">
            <a href="/" className="topbar-link">DASHBOARD</a>
            <a href="/leaderboard" className="topbar-link">LEADERBOARD</a>
            <a href="/brief" className="topbar-link">BRIEF</a>
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

export default TrackRecord
