import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'

const GA_ID = import.meta.env.VITE_GA_ID
if (GA_ID) {
  const s = document.createElement('script')
  s.async = true
  s.src = `https://www.googletagmanager.com/gtag/js?id=${GA_ID}`
  document.head.appendChild(s)
  window.dataLayer = window.dataLayer || []
  window.gtag = function () { window.dataLayer.push(arguments) }
  window.gtag('js', new Date())
  window.gtag('config', GA_ID)
}

// Strip GA cross-domain linker params from the visible URL after gtag has had a
// chance to read them for session stitching. Without the delay, we'd break
// cross-domain attribution from sentimentfx.org → app.sentimentfx.org.
setTimeout(() => {
  const url = new URL(window.location.href)
  const junk = ['_gl', '_ga', '_gid', '_gat']
  let changed = false
  for (const k of junk) {
    if (url.searchParams.has(k)) {
      url.searchParams.delete(k)
      changed = true
    }
  }
  if (changed) {
    const qs = url.searchParams.toString()
    window.history.replaceState(null, '', url.pathname + (qs ? '?' + qs : '') + url.hash)
  }
}, 800)

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
