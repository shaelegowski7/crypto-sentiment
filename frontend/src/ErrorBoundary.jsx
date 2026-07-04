import { Component } from "react"

// Last line of defense: without this, any uncaught render error (a null
// field from the API the component didn't guard against, a bad prop, etc.)
// unmounts the entire React tree and the user sees a black screen with no
// way back short of a hard refresh. Catches it, shows a recoverable message
// instead, and reports the error so it doesn't fail silently.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error, info) {
    console.error("[ErrorBoundary] Uncaught render error:", error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="dashboard" style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" }}>
          <div style={{ maxWidth: "420px", textAlign: "center" }}>
            <div className="logo" style={{ marginBottom: "16px" }}>SentimentFX</div>
            <p style={{ color: "var(--text)", fontSize: "14px", lineHeight: 1.6, marginBottom: "20px" }}>
              Something went wrong rendering this page. This has been logged — try reloading, or pick a different ticker.
            </p>
            <button className="upgrade-btn" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
