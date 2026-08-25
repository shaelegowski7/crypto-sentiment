// Fear & Greed-style semicircle gauge for the 0-100 sentiment score (50 =
// neutral). `score` is already display-scaled (see toSentimentScale in
// lib/constants.js) — this component does no -1..1 conversion of its own.
export default function SentimentGauge({ score, size = 148 }) {
  const hasScore = score !== null && score !== undefined && !Number.isNaN(score)
  const clamped = hasScore ? Math.max(0, Math.min(100, score)) : 50

  const cx = size / 2
  const cy = size / 2 + 2
  const r = size / 2 - 16
  const strokeWidth = 9

  const angleRad = ((180 - (clamped / 100) * 180) * Math.PI) / 180
  const startX = cx - r
  const endX = cx + r
  const needleLen = r * 0.8
  const needleX = cx + needleLen * Math.cos(angleRad)
  const needleY = cy - needleLen * Math.sin(angleRad)

  const label = !hasScore ? "NO DATA"
    : clamped >= 70 ? "BULLISH"
    : clamped >= 55 ? "LEANING BULLISH"
    : clamped > 45 ? "NEUTRAL"
    : clamped > 30 ? "LEANING BEARISH"
    : "BEARISH"
  const labelColor = !hasScore ? "var(--muted)"
    : clamped >= 55 ? "var(--positive)"
    : clamped <= 45 ? "var(--negative)"
    : "var(--muted)"

  const gradId = "sentGaugeGrad"

  return (
    <div className="sentiment-gauge">
      {/* Fluid: the viewBox does the scaling, so the gauge shrinks to fit
          narrow columns (mobile stacks .signal-metric to ~50% width) instead
          of forcing its cell taller than the metrics beside it. */}
      <svg
        width="100%"
        viewBox={`0 0 ${size} ${size / 2 + 22}`}
        style={{ maxWidth: `${size}px`, height: "auto", display: "block" }}
      >
        <defs>
          <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#f26d64" />
            <stop offset="50%" stopColor="#f0b429" />
            <stop offset="100%" stopColor="#42c768" />
          </linearGradient>
        </defs>
        <path
          d={`M ${startX} ${cy} A ${r} ${r} 0 0 1 ${endX} ${cy}`}
          fill="none"
          stroke={`url(#${gradId})`}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          opacity={hasScore ? 1 : 0.35}
        />
        {hasScore && (
          <>
            <line x1={cx} y1={cy} x2={needleX} y2={needleY} stroke="#e8ecf3" strokeWidth="2.5" strokeLinecap="round" />
            <circle cx={cx} cy={cy} r="4" fill="#e8ecf3" />
          </>
        )}
        <text x={startX} y={cy + 15} textAnchor="middle" fontFamily="var(--mono)" fontSize="9" fill="#8b95a7">0</text>
        <text x={cx} y={cy - r - 4} textAnchor="middle" fontFamily="var(--mono)" fontSize="9" fill="#8b95a7">50</text>
        <text x={endX} y={cy + 15} textAnchor="middle" fontFamily="var(--mono)" fontSize="9" fill="#8b95a7">100</text>
      </svg>
      <div className="sentiment-gauge-value">
        {hasScore ? Math.round(clamped) : "—"}
      </div>
      <div className="sentiment-gauge-label" style={{ color: labelColor }}>
        {label}
      </div>
    </div>
  )
}
