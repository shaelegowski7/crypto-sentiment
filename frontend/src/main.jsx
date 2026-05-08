import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

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

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
