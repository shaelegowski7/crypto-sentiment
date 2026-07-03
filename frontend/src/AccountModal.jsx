import { useState } from "react"
import { supabase } from "./supabaseClient"

const API = "https://api.sentimentfx.org"

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
      <div className="auth-overlay" onClick={onClose}>
        <div className="auth-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Delete account">
          <button className="auth-close" onClick={onClose} aria-label="Close">×</button>
          <div className="auth-logo">SentimentFX</div>
          <div className="auth-subtitle">Delete account</div>

          <div className="acct-warn">
            This will cancel your subscription, delete your alerts and API key, and permanently remove your account.
            This action cannot be undone.
          </div>
          <div className="acct-warn-note">Your data will be permanently deleted.</div>

          {error && <div className="auth-error">{error}</div>}

          <div className="acct-btn-row">
            <button className="acct-btn" onClick={() => { setView("main"); setError(null) }}>Cancel</button>
            <button className="acct-btn-danger-solid" onClick={handleDeleteAccount} disabled={deleteLoading}>
              {deleteLoading ? "Deleting..." : "Delete Account"}
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="auth-overlay" onClick={onClose}>
      <div className="auth-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Account settings">
        <button className="auth-close" onClick={onClose} aria-label="Close">×</button>
        <div className="auth-logo">SentimentFX</div>
        <div className="auth-subtitle">Account settings</div>

        {/* Profile */}
        <div className="acct-row">
          <span className="acct-muted">{user.email}</span>
          <span className={`tier-badge tier-${tier}`}>{tier}</span>
        </div>

        {/* Subscription */}
        <div className="acct-section">
          <div className="acct-section-label">Subscription</div>
          {isPro ? (
            <div className="acct-row">
              <span className="acct-muted">Pro plan active</span>
              <button className="acct-btn-accent" onClick={handleBilling} disabled={billingLoading}>
                {billingLoading ? "..." : "Manage Billing →"}
              </button>
            </div>
          ) : (
            <div className="acct-stack">
              <span className="acct-muted">
                Upgrade to Pro for all tickers, full history, alerts, and CSV export.
              </span>
              <div className="acct-btn-row">
                <button
                  className="acct-btn-accent"
                  onClick={() => {
                    window.location.href = `${window.location.origin}?upgrade=monthly`
                    onClose()
                  }}
                >
                  £11.99 / mo
                </button>
                <button
                  className="acct-btn-accent"
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
        <div className="acct-section">
          <div className="acct-section-label">Preferences</div>
          <div className="acct-row">
            <div>
              <div className="acct-pref-title">Morning Brief</div>
              <div className="acct-pref-sub">Daily 7am email digest</div>
            </div>
            <button
              className={`acct-toggle ${briefEnabled ? "on" : ""}`}
              onClick={handleBriefToggle}
              disabled={briefLoading}
              role="switch"
              aria-checked={briefEnabled}
              aria-label="Toggle morning brief"
            >
              <div className="acct-toggle-knob" />
            </button>
          </div>
        </div>

        {/* Security */}
        <div className="acct-section">
          <div className="acct-section-label">Security</div>
          {resetSent ? (
            <div className="auth-success">Reset link sent — check your inbox.</div>
          ) : (
            <button className="acct-btn" onClick={handlePasswordReset} disabled={resetLoading}>
              {resetLoading ? "Sending..." : "Send password reset email"}
            </button>
          )}
        </div>

        {/* Danger zone */}
        <div className="acct-section">
          <div className="acct-section-label">Danger zone</div>
          <button className="acct-btn-danger" onClick={() => setView("confirm-delete")}>
            Delete account
          </button>
        </div>

        {/* Footer */}
        <div className="acct-footer">
          <button className="acct-btn-signout" onClick={onSignOut}>Sign Out</button>
        </div>
      </div>
    </div>
  )
}
