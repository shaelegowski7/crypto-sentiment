import { useState, useEffect, useRef } from "react"
import axios from "axios"
import { supabase } from "../supabaseClient"
import AuthModal from "../AuthModal"
import { API, redirectToCheckout } from "../lib/constants"
import "../dashboard.css"

function setMeta(name, content) {
  let el = document.querySelector(`meta[name="${name}"]`)
  if (!el) { el = document.createElement("meta"); el.setAttribute("name", name); document.head.appendChild(el) }
  el.setAttribute("content", content)
}

function fmtDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" })
}

function BriefCard({ brief }) {
  const preview = (brief.ai_summary || "").length > 220
    ? brief.ai_summary.slice(0, 220).trim() + "…"
    : brief.ai_summary
  return (
    <a href={`/brief/${brief.date}`} className="brief-card">
      <div className="brief-card-header">
        <span className="brief-card-date">{fmtDate(brief.date)}</span>
        <div className="brief-card-tickers">
          {brief.tickers.map(t => <span key={t} className="ticker-chip">{t}</span>)}
        </div>
      </div>
      <p className="brief-card-summary">{preview}</p>
      <span className="brief-card-readmore">Read full brief →</span>
    </a>
  )
}

// The embedded brief is a complete standalone HTML document (the actual email
// body sent to subscribers) — an iframe with srcDoc is the correct render
// target, not dangerouslySetInnerHTML, since a full <html>/<head> document
// can't be injected as a fragment. Height is measured after load so the
// iframe fits its content instead of showing internal scrollbars.
function EmbeddedBrief({ html }) {
  const ref = useRef(null)
  const [height, setHeight] = useState(400)

  return (
    <iframe
      ref={ref}
      srcDoc={html}
      title="Morning brief"
      style={{ width: "100%", height: `${height}px`, border: "none", borderRadius: "8px", background: "#080c10" }}
      onLoad={() => {
        try {
          const doc = ref.current.contentWindow.document
          setHeight(doc.documentElement.scrollHeight + 40)
        } catch {
          // cross-origin or measurement failure — keep the fallback height
        }
      }}
    />
  )
}

function ListView({ session, openAuth }) {
  const [archive, setArchive] = useState(null)

  useEffect(() => {
    document.title = "Morning Brief Archive — daily crypto sentiment digest · SentimentFX"
    setMeta("description",
      "Every daily SentimentFX morning brief, archived. FinBERT-scored sentiment vs price for BTC, ETH and XRP, " +
      "with an AI-generated summary of the day's biggest divergence signal.")
  }, [])

  useEffect(() => {
    let cancelled = false
    axios.get(`${API}/brief/archive?limit=30`)
      .then(r => { if (!cancelled) setArchive(r.data.briefs) })
      .catch(() => { if (!cancelled) setArchive([]) })
    return () => { cancelled = true }
  }, [])

  return (
    <>
      <div style={{ marginBottom: "24px" }}>
        <h1 style={{ fontFamily: "var(--mono)", fontSize: "22px", fontWeight: 500, color: "var(--text)", letterSpacing: "0.02em", marginBottom: "8px" }}>
          Morning Brief Archive
        </h1>
        <p style={{ fontFamily: "var(--sans)", fontSize: "13px", color: "var(--muted)", maxWidth: "680px", lineHeight: 1.5 }}>
          Sent every day at 7am. An AI-written summary of the sharpest sentiment/price divergence across BTC, ETH and XRP,
          plus the full per-ticker breakdown subscribers get by email.
        </p>
      </div>

      {!session && (
        <div className="upgrade-banner" style={{ marginBottom: "20px" }}>
          <span className="upgrade-text">
            <strong>Free preview:</strong> read the AI summary for every day · Sign in to unlock the full per-ticker breakdown and get it by email each morning.
          </span>
          <button className="upgrade-btn" onClick={() => openAuth("signup")}>Sign In / Sign Up</button>
        </div>
      )}

      {archive === null && (
        <div className="brief-list">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="brief-card">
              <div className="skeleton" style={{ height: "14px", width: "180px", marginBottom: "12px" }} />
              <div className="skeleton" style={{ height: "12px", width: "100%", marginBottom: "6px" }} />
              <div className="skeleton" style={{ height: "12px", width: "70%" }} />
            </div>
          ))}
        </div>
      )}

      {archive?.length === 0 && (
        <div className="panel">
          <div className="panel-body" style={{ color: "var(--muted)", fontSize: "13px" }}>
            No briefs yet — check back tomorrow morning.
          </div>
        </div>
      )}

      {archive && archive.length > 0 && (
        <div className="brief-list">
          {archive.map(b => <BriefCard key={b.date} brief={b} />)}
        </div>
      )}
    </>
  )
}

function DetailView({ date, session, openAuth }) {
  const [brief, setBrief] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setBrief(null)
    setError(null)
    const headers = session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}
    axios.get(`${API}/brief/${date}`, { headers })
      .then(r => { if (!cancelled) setBrief(r.data) })
      .catch(() => { if (!cancelled) setError("No brief found for that date.") })
    return () => { cancelled = true }
  }, [date, session])

  useEffect(() => {
    if (!brief) return
    document.title = `${fmtDate(brief.date)} Morning Brief · SentimentFX`
    setMeta("description", (brief.ai_summary || "").slice(0, 300))
  }, [brief])

  return (
    <>
      <a href="/brief" className="topbar-link" style={{ display: "inline-block", marginBottom: "20px" }}>← All briefs</a>

      {error && (
        <div className="panel"><div className="panel-body" style={{ color: "var(--negative)", fontSize: "13px" }}>{error}</div></div>
      )}

      {!error && !brief && (
        <div className="panel">
          <div className="panel-body">
            <div className="skeleton" style={{ height: "16px", width: "240px", marginBottom: "16px" }} />
            <div className="skeleton" style={{ height: "200px", width: "100%" }} />
          </div>
        </div>
      )}

      {brief && (
        <>
          <div style={{ marginBottom: "16px" }}>
            <h1 style={{ fontFamily: "var(--mono)", fontSize: "20px", fontWeight: 500, color: "var(--text)", marginBottom: "8px" }}>
              {fmtDate(brief.date)}
            </h1>
            <div className="brief-card-tickers">
              {brief.tickers.map(t => <span key={t} className="ticker-chip">{t}</span>)}
            </div>
          </div>

          <div className="panel" style={{ marginBottom: "20px" }}>
            <div className="panel-header"><span className="panel-title">AI Summary</span></div>
            <div className="panel-body">
              <p style={{ fontSize: "14px", lineHeight: 1.65, color: "var(--text)", margin: 0 }}>{brief.ai_summary}</p>
            </div>
          </div>

          {brief.is_paywalled ? (
            <div className="upgrade-banner">
              <span className="upgrade-text">
                <strong>Full breakdown locked:</strong> the per-ticker sentiment, price and divergence data — exactly what subscribers received by email — needs Pro.
              </span>
              {session ? (
                <div style={{ display: "flex", gap: "8px" }}>
                  <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TNx0H2NzVdYK0wrPwt0Rhcw")}>£11.99 / mo</button>
                  <button className="upgrade-btn" onClick={() => redirectToCheckout("price_1TNx0K2NzVdYK0wrcGf1mz1s")}>£99.99 / yr</button>
                </div>
              ) : (
                <button className="upgrade-btn" onClick={() => openAuth("signup")}>Sign In / Sign Up</button>
              )}
            </div>
          ) : (
            <div className="panel">
              <div className="panel-header"><span className="panel-title">Full Brief — as sent by email</span></div>
              <div className="panel-body">
                <EmbeddedBrief html={brief.content_html} />
              </div>
            </div>
          )}
        </>
      )}
    </>
  )
}

function Brief() {
  const pathname = typeof window !== "undefined" ? window.location.pathname : "/brief"
  const dateParam = pathname.startsWith("/brief/") ? pathname.slice("/brief/".length) : null

  const [session, setSession] = useState(null)
  const [showAuth, setShowAuth] = useState(false)
  const [authMode, setAuthMode] = useState("login")

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => setSession(session))
    return () => subscription.unsubscribe()
  }, [])

  const openAuth = (mode) => { setAuthMode(mode); setShowAuth(true) }

  return (
    <>
      <div className="dashboard">
        <header className="topbar">
          <div className="topbar-left">
            <a href="https://sentimentfx.org" className="logo">SentimentFX</a>
            <div className="logo-divider" />
            <span className="tagline">MORNING BRIEF</span>
          </div>
          <div className="topbar-right">
            <a href="/" className="topbar-link">DASHBOARD</a>
            <a href="/leaderboard" className="topbar-link">LEADERBOARD</a>
            <a href="/track-record" className="topbar-link">TRACK RECORD</a>
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
          {dateParam
            ? <DetailView date={dateParam} session={session} openAuth={openAuth} />
            : <ListView session={session} openAuth={openAuth} />}
        </main>
      </div>

      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialMode={authMode} />}
    </>
  )
}

export default Brief
