import { useState, useEffect } from "react"
import axios from "axios"
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer
} from "recharts"

const TICKERS = ["BTC", "ETH", "SOL", "XRP"]
const API = "http://127.0.0.1:8000"

export default function App() {
  const [ticker, setTicker] = useState("BTC")
  const [data, setData] = useState([])
  const [headlines, setHeadlines] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchDashboard()
  }, [ticker])

  const fetchDashboard = async () => {
    setLoading(true)
    try {
      const res = await axios.get(`${API}/dashboard/${ticker}`)
      const { sentiment, prices } = res.data

      // Merge prices and sentiment by date
      const priceMap = {}
      prices.forEach(p => {
        const date = p.date.split("T")[0]
        priceMap[date] = p.close_price
      })

      const merged = sentiment.map(s => ({
        date: s.date.split("T")[0],
        sentiment: parseFloat(s.score.toFixed(2)),
        price: priceMap[s.date.split("T")[0]] || null,
        label: s.label,
        title: s.title
      }))

      setData(merged)
      setHeadlines(sentiment)
    } catch (err) {
      console.error(err)
    }
    setLoading(false)
  }

  const sentimentColor = (label) => {
    if (label === "positive") return "#22c55e"
    if (label === "negative") return "#ef4444"
    return "#94a3b8"
  }

  return (
    <div style={{ fontFamily: "sans-serif", padding: "2rem", background: "#0f172a", minHeight: "100vh", color: "#f1f5f9" }}>
      <h1 style={{ fontSize: "1.8rem", marginBottom: "0.5rem" }}>📈 Crypto Sentiment Dashboard</h1>
      <p style={{ color: "#94a3b8", marginBottom: "1.5rem" }}>News sentiment vs price — powered by FinBERT</p>

      {/* Ticker selector */}
      <div style={{ marginBottom: "2rem", display: "flex", gap: "0.5rem" }}>
        {TICKERS.map(t => (
          <button
            key={t}
            onClick={() => setTicker(t)}
            style={{
              padding: "0.5rem 1.2rem",
              borderRadius: "8px",
              border: "none",
              cursor: "pointer",
              background: ticker === t ? "#6366f1" : "#1e293b",
              color: "#f1f5f9",
              fontWeight: ticker === t ? "bold" : "normal"
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {loading ? <p>Loading...</p> : (
        <>
          {/* Chart */}
          <div style={{ background: "#1e293b", borderRadius: "12px", padding: "1.5rem", marginBottom: "2rem" }}>
            <h2 style={{ marginBottom: "1rem" }}>{ticker} — Sentiment vs Price</h2>
            <ResponsiveContainer width="100%" height={350}>
              <ComposedChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="date" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="price" orientation="right" stroke="#94a3b8" />
                <YAxis yAxisId="sentiment" orientation="left" domain={[-1, 1]} stroke="#94a3b8" />
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid #334155" }}
                  formatter={(value, name) => {
                    if (name === "Price (£)") return [`£${value}`, "Price"]
                    return [value, "Sentiment Score"]
                  }}
                />
                <Legend />
                <Bar yAxisId="sentiment" dataKey="sentiment" fill="#6366f1" opacity={0.7} name="Sentiment Score" />
                <Line yAxisId="price" type="monotone" dataKey="price" stroke="#f59e0b" dot={false} name="Price (£)" strokeWidth={2} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* Headlines */}
          <div style={{ background: "#1e293b", borderRadius: "12px", padding: "1.5rem" }}>
            <h2 style={{ marginBottom: "1rem" }}>Latest Headlines</h2>
            {headlines.map((h, i) => (
              <div key={i} style={{
                padding: "0.75rem",
                marginBottom: "0.5rem",
                borderRadius: "8px",
                background: "#0f172a",
                borderLeft: `4px solid ${sentimentColor(h.label)}`
              }}>
                <p style={{ margin: 0, fontSize: "0.9rem" }}>{h.title}</p>
                <p style={{ margin: "0.25rem 0 0", fontSize: "0.75rem", color: sentimentColor(h.label) }}>
                  {h.label} • score: {h.score}
                </p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
