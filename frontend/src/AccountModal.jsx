import { useState } from "react"
import { supabase } from "./supabaseClient"

const API = "https://api.sentimentfx.org"

const s = {
  overlay: {
    position: "fixed", inset: 0, background: "rgba(8,12,16,0.85)",
    backdropFilter: "blur(4px)", zIndex: 200,
    display: "flex", alignItems: "center", justifyContent: "center",
  },
  modal: {
    background: "#0d1117", border: "1px solid #21262d", borderRadius: "4px",
    padding: "40px", width: "100%", maxWidth: "440px", position: "relative",
    maxHeight: "90vh", overflowY: "auto",
  },
  close: {
    position: "absolute", top: "16px", right: "16px",
    background: "transparent", border: "none", color: "#7d8590",
    cursor: "pointer", fontFamily: "IBM Plex Mono", fontSize: "18px",
  },
  logo: {
    fontFamily: "IBM Plex Mono", fontSize: "13px", fontWeight: 600,
    letterSpacing: "0.2em", color: "#f0b429", textTransform: "uppercase",
    marginBottom: "4px",
  },
  subtitle: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#7d8590",
    letterSpacing: "0.1em", marginBottom: "32px",
  },
  section: {
    borderTop: "1px solid #21262d", paddingTop: "20px", marginTop: "20px",
  },
  sectionLabel: {
    fontFamily: "IBM Plex Mono", fontSize: "9px", letterSpacing: "0.15em",
    color: "#7d8590", textTransform: "uppercase", marginBottom: "12px",
  },
  row: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    fontFamily: "IBM Plex Mono", fontSize: "11px", color: "#e6edf3",
  },
  muted: { color: "#7d8590" },
  tierBadge: (tier) => ({
    fontFamily: "IBM Plex Mono", fontSize: "9px", letterSpacing: "0.12em",
    padding: "2px 8px", borderRadius: "2px", textTransform: "uppercase",
    background: tier === "pro" || tier === "data" ? "rgba(88,166,255,0.1)" : "rgba(139,148,158,0.1)",
    color: tier === "pro" || tier === "data" ? "#58a6ff" : "#7d8590",
    border: tier === "pro" || tier === "data" ? "1px solid rgba(88,166,255,0.3)" : "1px solid rgba(139,148,158,0.2)",
  }),
  btn: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", letterSpacing: "0.08em",
    padding: "6px 14px", border: "1px solid #30363d", borderRadius: "2px",
    background: "transparent", color: "#e6edf3", cursor: "pointer",
    textTransform: "uppercase",
  },
  btnAccent: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", letterSpacing: "0.08em",
    padding: "6px 14px", border: "1px solid rgba(240,180,41,0.4)", borderRadius: "2px",
    background: "rgba(240,180,41,0.06)", color: "#f0b429", cursor: "pointer",
    textTransform: "uppercase",
  },
  btnDanger: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", letterSpacing: "0.08em",
    padding: "6px 14px", border: "1px solid rgba(248,81,73,0.4)", borderRadius: "2px",
    background: "transparent", color: "#f85149", cursor: "pointer",
    textTransform: "uppercase",
  },
  btnDangerSolid: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", letterSpacing: "0.08em",
    padding: "8px 16px", border: "none", borderRadius: "2px",
    background: "#f85149", color: "#fff", cursor: "pointer",
    textTransform: "uppercase", fontWeight: 600,
  },
  toggle: (on) => ({
    width: "36px", height: "20px", borderRadius: "10px", cursor: "pointer",
    background: on ? "#3fb950" : "#30363d", position: "relative",
    border: "none", flexShrink: 0, transition: "background 0.15s",
  }),
  toggleKnob: (on) => ({
    position: "absolute", top: "3px", left: on ? "19px" : "3px",
    width: "14px", height: "14px", borderRadius: "50%",
    background: "#fff", transition: "left 0.15s",
  }),
  success: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#3fb950",
    letterSpacing: "0.05em", marginTop: "8px",
  },
  warn: {
    fontFamily: "IBM Plex Mono", fontSize: "11px", color: "#e6edf3",
    lineHeight: 1.7, marginBottom: "20px",
  },
  warnNote: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#f85149",
    marginBottom: "20px",
  },
  footer: {
    borderTop: "1px solid #21262d", paddingTop: "20px", marginTop: "20px",
    display: "flex", justifyContent: "flex-end",
  },
  btnSignOut: {
    fontFamily: "IBM Plex Mono", fontSize: "10px", letterSpacing: "0.08em",
    padding: "6px 14px", border: "1px solid #21262d", borderRadius: "2px",
    background: "transparent", color: "#7d8590", cursor: "pointer",
    textTransform: "uppercase",
  },
}

export default function AccountModal({ user, session, profile, onClose, onSignOut, onProfileUpdate }) {
  const [view, setView] = useState("main")
  const [briefEnabled, setBriefEnabled] = useState(profile?.morning_brief_enabled ?? false)
  const [briefLoading, setBriefLoading] = useState(false)
  const [resetSent, setResetSent] = useState(false)
  const [resetLoading, setResetLoading] = useState(false)
  const [billingLoading, setBillingLoading] = useState(false)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [error, setError] = useState(null)

  const tier = profile?.tier ?? "free"
  const isPro = tier === "pro" || tier === "data"

  const handleBriefToggle = async () => {
    setBriefLoading(true)
    const next = !briefEnabled
    try {
      const res = await fetch(`${API}/profile/brief`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ enabled: next }),
      })
      if (!res.ok) throw new Error("Failed to update")
      setBriefEnabled(next)
      onProfileUpdate?.({ ...profile, morning_brief_enabled: next })
    } catch {
      // silent — no change
    }
    setBriefLoading(false)
  }

  const handlePasswordReset = async () => {
    setResetLoading(true)
    await supabase.auth.resetPasswordForEmail(user.email, {
      redirectTo: "https://app.sentimentfx.org",
    })
    setResetSent(true)
    setResetLoading(false)
  }

  const handleBilling = async () => {
    setBillingLoading(true)
    try {
      const res = await fetch(`${API}/billing-portal`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      const data = await res.json()
      if (data.url) window.location.href = data.url
      else setError("Could not open billing portal.")
    } catch {
      setError("Could not open billing portal.")
    }
    setBillingLoading(false)
  }

  const handleDeleteAccount = async () => {
    setDeleteLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/account`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      if (!res.ok) throw new Error("Delete failed")
      await supabase.auth.signOut()
      onClose()
    } catch {
      setError("Failed to delete account. Please try again or contact support.")
      setDeleteLoading(false)
    }
  }

  if (view === "confirm-delete") {
    return (
      <div style={s.overlay} onClick={onClose}>
        <div style={s.modal} onClick={e => e.stopPropagation()}>
          <button style={s.close} onClick={onClose}>×</button>
          <div style={s.logo}>SentimentFX</div>
          <div style={s.subtitle}>DELETE ACCOUNT</div>

          <div style={s.warn}>
            This will cancel your subscription, delete your alerts and API key, and permanently remove your account.
            This action cannot be undone.
          </div>
          <div style={s.warnNote}>Your data will be permanently deleted.</div>

          {error && (
            <div style={{ ...s.warnNote, marginBottom: "16px" }}>{error}</div>
          )}

          <div style={{ display: "flex", gap: "12px" }}>
            <button style={s.btn} onClick={() => { setView("main"); setError(null) }}>Cancel</button>
            <button style={s.btnDangerSolid} onClick={handleDeleteAccount} disabled={deleteLoading}>
              {deleteLoading ? "Deleting..." : "Delete Account"}
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={s.overlay} onClick={onClose}>
      <div style={s.modal} onClick={e => e.stopPropagation()}>
        <button style={s.close} onClick={onClose}>×</button>
        <div style={s.logo}>SentimentFX</div>
        <div style={s.subtitle}>ACCOUNT SETTINGS</div>

        {/* Profile */}
        <div style={s.row}>
          <span style={s.muted}>{user.email}</span>
          <span style={s.tierBadge(tier)}>{tier}</span>
        </div>

        {/* Subscription */}
        <div style={s.section}>
          <div style={s.sectionLabel}>Subscription</div>
          {isPro ? (
            <div style={s.row}>
              <span style={s.muted}>Pro plan active</span>
              <button style={s.btnAccent} onClick={handleBilling} disabled={billingLoading}>
                {billingLoading ? "..." : "Manage Billing →"}
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <span style={{ ...s.muted, fontFamily: "IBM Plex Mono", fontSize: "11px" }}>
                Upgrade to Pro for all tickers, full history, alerts, and CSV export.
              </span>
              <div style={{ display: "flex", gap: "8px" }}>
                <button
                  style={s.btnAccent}
                  onClick={() => {
                    window.location.href = `${window.location.origin}?upgrade=monthly`
                    onClose()
                  }}
                >
                  £11.99 / mo
                </button>
                <button
                  style={s.btnAccent}
                  onClick={() => {
                    window.location.href = `${window.location.origin}?upgrade=annual`
                    onClose()
                  }}
                >
                  £99.99 / yr
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Preferences */}
        <div style={s.section}>
          <div style={s.sectionLabel}>Preferences</div>
          <div style={s.row}>
            <div>
              <div style={{ fontFamily: "IBM Plex Mono", fontSize: "11px", marginBottom: "2px" }}>Morning Brief</div>
              <div style={{ ...s.muted, fontFamily: "IBM Plex Mono", fontSize: "10px" }}>Daily 7am email digest</div>
            </div>
            <button
              style={s.toggle(briefEnabled)}
              onClick={handleBriefToggle}
              disabled={briefLoading}
              aria-label="Toggle morning brief"
            >
              <div style={s.toggleKnob(briefEnabled)} />
            </button>
          </div>
        </div>

        {/* Security */}
        <div style={s.section}>
          <div style={s.sectionLabel}>Security</div>
          {resetSent ? (
            <div style={s.success}>Reset link sent — check your inbox.</div>
          ) : (
            <button style={s.btn} onClick={handlePasswordReset} disabled={resetLoading}>
              {resetLoading ? "Sending..." : "Send password reset email"}
            </button>
          )}
        </div>

        {/* Danger zone */}
        <div style={s.section}>
          <div style={s.sectionLabel}>Danger zone</div>
          <button style={s.btnDanger} onClick={() => setView("confirm-delete")}>
            Delete account
          </button>
        </div>

        {/* Footer */}
        <div style={s.footer}>
          <button style={s.btnSignOut} onClick={onSignOut}>Sign Out</button>
        </div>
      </div>
    </div>
  )
}
