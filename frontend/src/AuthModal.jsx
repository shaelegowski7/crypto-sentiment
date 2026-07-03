import { useState } from "react"
import { supabase } from "./supabaseClient"

function friendlyError(message) {
  if (!message) return "Something went wrong. Please try again."
  const m = message.toLowerCase()
  if (m.includes("password") && (m.includes("characters") || m.includes("uppercase") || m.includes("abcdefg")))
    return "Password must be at least 8 characters and include an uppercase letter, a number, and a symbol."
  if (m.includes("invalid login") || m.includes("invalid credentials") || m.includes("email not confirmed"))
    return "Incorrect email or password."
  if (m.includes("user already registered") || m.includes("already been registered"))
    return "An account with this email already exists. Try signing in."
  if (m.includes("rate limit"))
    return "Too many attempts. Please wait a moment and try again."
  if (m.includes("unable to validate email"))
    return "Please enter a valid email address."
  return message
}

export default function AuthModal({ onClose, initialMode = "login" }) {
  const [mode, setMode] = useState(initialMode)
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [resetSent, setResetSent] = useState(false)

  const handlePasswordReset = async () => {
    setLoading(true)
    setError(null)
    try {
      const { error } = await supabase.auth.resetPasswordForEmail(email, {
        // Return the user to whichever page they triggered the reset from
        // (/leaderboard, /track-record, /) rather than always punting them
        // to the dashboard root.
        redirectTo: typeof window !== "undefined" ? window.location.href : "https://app.sentimentfx.org",
      })
      if (error) throw error
      setResetSent(true)
    } catch (err) {
      setError(friendlyError(err.message))
    }
    setLoading(false)
  }

  const handleEmailAuth = async () => {
    setLoading(true)
    setError(null)
    try {
      if (mode === "signup") {
        const { error } = await supabase.auth.signUp({ email, password })
        if (error) throw error
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      }
      onClose()
    } catch (err) {
      setError(friendlyError(err.message))
    }
    setLoading(false)
  }

  const handleGoogle = async () => {
    await supabase.auth.signInWithOAuth({
      provider: "google",
      // window.location.href (not .origin) so the OAuth round-trip lands the
      // user back on the same path they started from — /leaderboard,
      // /track-record, or /.  Without this, every Google login dumps users
      // on the dashboard regardless of where they clicked Log In.
      options: { redirectTo: window.location.href }
    })
  }

  return (
    <div className="auth-overlay" onClick={onClose}>
      <div className="auth-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Authentication">
        <button className="auth-close" onClick={onClose} aria-label="Close">×</button>

        <div className="auth-logo">SentimentFX</div>
        <div className="auth-subtitle">
          {mode === "login" ? "Sign in to your account" : mode === "signup" ? "Create an account" : "Reset password"}
        </div>

        {mode === "reset" ? (
          resetSent ? (
            <div className="auth-success">Reset link sent — check your inbox.</div>
          ) : (
            <>
              <input
                className="auth-input"
                type="email"
                placeholder="your@email.com"
                value={email}
                onChange={e => setEmail(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handlePasswordReset()}
              />
              {error && <div className="auth-error">{error}</div>}
              <button className="auth-btn-primary" onClick={handlePasswordReset} disabled={loading}>
                {loading ? "..." : "Send reset link"}
              </button>
            </>
          )
        ) : (
          <>
            <button className="auth-btn-google" onClick={handleGoogle}>
              Continue with Google
            </button>

            <div className="auth-divider">
              <div className="auth-divider-line" />
              <span className="auth-divider-text">OR</span>
              <div className="auth-divider-line" />
            </div>

            <input
              className="auth-input"
              type="email"
              placeholder="your@email.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleEmailAuth()}
            />
            <input
              className="auth-input"
              type="password"
              placeholder="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleEmailAuth()}
            />

            {mode === "login" && (
              <div className="auth-forgot-row">
                <span className="auth-forgot" onClick={() => { setError(null); setMode("reset") }}>
                  Forgot password?
                </span>
              </div>
            )}

            {error && <div className="auth-error">{error}</div>}

            <button className="auth-btn-primary" onClick={handleEmailAuth} disabled={loading}>
              {loading ? "..." : mode === "login" ? "Sign In" : "Create Account"}
            </button>
          </>
        )}

        <div className="auth-toggle">
          {mode === "reset" ? (
            <span className="auth-toggle-link" onClick={() => { setMode("login"); setResetSent(false); setError(null) }}>
              Back to sign in
            </span>
          ) : mode === "login" ? (
            <>No account?{" "}
              <span className="auth-toggle-link" onClick={() => setMode("signup")}>Sign up</span>
            </>
          ) : (
            <>Already have an account?{" "}
              <span className="auth-toggle-link" onClick={() => setMode("login")}>Sign in</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
