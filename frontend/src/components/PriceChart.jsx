// Dependency-free SVG chart: close line + EMA overlays.
export default function PriceChart({ series, height = 280 }) {
  if (!series || series.length < 2) return <div className="loading">No price series.</div>

  const W = 900
  const H = height
  const pad = { top: 10, right: 10, bottom: 22, left: 54 }
  const iw = W - pad.left - pad.right
  const ih = H - pad.top - pad.bottom

  const closes = series.map((r) => r[4])
  const dates = series.map((r) => r[0])
  let min = Math.min(...closes)
  let max = Math.max(...closes)
  const range = max - min || 1
  min -= range * 0.05
  max += range * 0.05

  const x = (i) => pad.left + (i / (series.length - 1)) * iw
  const y = (v) => pad.top + (1 - (v - min) / (max - min)) * ih

  const line = (vals) => vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')

  // ~6 evenly spaced date labels
  const ticks = []
  const step = Math.ceil(series.length / 6)
  for (let i = 0; i < series.length; i += step) ticks.push(i)
  ticks.push(series.length - 1)

  const last = closes[closes.length - 1]
  const prev = closes[closes.length - 2]
  const pct = prev ? ((last - prev) / prev) * 100 : 0
  const color = pct >= 0 ? 'var(--up)' : 'var(--down)'

  const gridY = []
  const yStep = (max - min) / 5
  for (let g = 0; g <= 5; g++) {
    const v = min + g * yStep
    gridY.push({ v, y: y(v) })
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
        Close {last.toFixed(2)}{' '}
        <span style={{ color }}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</span>
        <span className="legend" style={{ marginLeft: 12 }}>— close, … EMA</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="price chart">
        {gridY.map((g, i) => (
          <g key={i}>
            <line x1={pad.left} x2={W - pad.right} y1={g.y} y2={g.y} stroke="var(--border)" strokeWidth="1" />
            <text x={pad.left - 6} y={g.y + 3} fill="var(--muted)" fontSize="10" textAnchor="end">
              {g.v.toFixed(0)}
            </text>
          </g>
        ))}
        <path d={line(closes)} fill="none" stroke="var(--accent)" strokeWidth="1.8" />
        {ticks.map((i) => (
          <text key={i} x={x(i)} y={H - 6} fill="var(--muted)" fontSize="10" textAnchor="middle">
            {dates[i]}
          </text>
        ))}
      </svg>
    </div>
  )
}
