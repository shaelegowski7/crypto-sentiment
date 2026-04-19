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
      options: { redirectTo: window.location.origin }
    })
  }

  const styles = {
    overlay: {
      position: "fixed", inset: 0, background: "rgba(8,12,16,0.85)",
      backdropFilter: "blur(4px)", zIndex: 200,
      display: "flex", alignItems: "center", justifyContent: "center",
    },
    modal: {
      background: "#0d1117", border: "1px solid #21262d", borderRadius: "4px",
      padding: "40px", width: "100%", maxWidth: "400px", position: "relative",
    },
    close: {
      position: "absolute", top: "16px", right: "16px",
      background: "transparent", border: "none", color: "#7d8590",
      cursor: "pointer", fontFamily: "IBM Plex Mono", fontSize: "18px",
    },
    logo: {
      fontFamily: "IBM Plex Mono", fontSize: "13px", fontWeight: 600,
      letterSpacing: "0.2em", color: "#f0b429", textTransform: "uppercase",
      marginBottom: "8px",
    },
    subtitle: {
      fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#7d8590",
      letterSpacing: "0.1em", marginBottom: "32px",
    },
    input: {
      width: "100%", background: "#161b22", border: "1px solid #30363d",
      borderRadius: "2px", padding: "10px 14px", fontFamily: "IBM Plex Mono",
      fontSize: "12px", color: "#e6edf3", outline: "none", marginBottom: "10px",
      boxSizing: "border-box",
    },
    btnPrimary: {
      width: "100%", background: "#f0b429", border: "none", borderRadius: "2px",
      padding: "12px", fontFamily: "IBM Plex Mono", fontSize: "11px",
      fontWeight: 600, letterSpacing: "0.1em", color: "#080c10",
      cursor: "pointer", textTransform: "uppercase", marginBottom: "10px",
    },
    btnGoogle: {
      width: "100%", background: "transparent", border: "1px solid #30363d",
      borderRadius: "2px", padding: "12px", fontFamily: "IBM Plex Mono",
      fontSize: "11px", letterSpacing: "0.1em", color: "#e6edf3",
      cursor: "pointer", textTransform: "uppercase", marginBottom: "24px",
    },
    toggle: {
      fontFamily: "IBM Plex Mono", fontSize: "11px", color: "#7d8590",
      textAlign: "center",
    },
    toggleLink: {
      color: "#f0b429", cursor: "pointer", textDecoration: "underline",
    },
    error: {
      fontFamily: "IBM Plex Mono", fontSize: "11px", color: "#f85149",
      marginBottom: "12px", letterSpacing: "0.05em",
    },
    divider: {
      display: "flex", alignItems: "center", gap: "12px", margin: "16px 0",
    },
    dividerLine: {
      flex: 1, height: "1px", background: "#21262d",
    },
    dividerText: {
      fontFamily: "IBM Plex Mono", fontSize: "10px", color: "#7d8590",
      letterSpacing: "0.1em",
    },
  }

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.modal} onClick={e => e.stopPropagation()}>
        <button style={styles.close} onClick={onClose}>×</button>

        <div style={styles.logo}>SentimentFX</div>
        <div style={styles.subtitle}>
          {mode === "login" ? "SIGN IN TO YOUR ACCOUNT" : "CREATE AN ACCOUNT"}
        </div>

        <button style={styles.btnGoogle} onClick={handleGoogle}>
          Continue with Google
        </button>

        <div style={styles.divider}>
          <div style={styles.dividerLine} />
          <span style={styles.dividerText}>OR</span>
          <div style={styles.dividerLine} />
        </div>

        <input
          style={styles.input}
          type="email"
          placeholder="your@email.com"
          value={email}
          onChange={e => setEmail(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleEmailAuth()}
        />
        <input
          style={styles.input}
          type="password"
          placeholder="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleEmailAuth()}
        />

        {error && <div style={styles.error}>{error}</div>}

        <button style={styles.btnPrimary} onClick={handleEmailAuth} disabled={loading}>
          {loading ? "..." : mode === "login" ? "Sign In" : "Create Account"}
        </button>

        <div style={styles.toggle}>
          {mode === "login" ? (
            <>No account?{" "}
              <span style={styles.toggleLink} onClick={() => setMode("signup")}>Sign up</span>
            </>
          ) : (
            <>Already have an account?{" "}
              <span style={styles.toggleLink} onClick={() => setMode("login")}>Sign in</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}