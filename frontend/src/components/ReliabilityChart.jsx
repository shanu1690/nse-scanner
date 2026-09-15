// Dependency-free SVG reliability curve -- mean predicted probability vs
// observed frequency per bin, with the y=x "perfectly calibrated" diagonal
// as a reference. Matches the project's existing no-extra-dependency chart
// style (see PayoffChart.jsx). Point radius scales with bin sample size so
// a thin bin visibly carries less weight than a fat one.
export default function ReliabilityChart({ curve, height = 260 }) {
  if (!curve || curve.length === 0) return <div className="loading">No reliability curve available.</div>

  const W = 500
  const H = height
  const pad = { top: 14, right: 14, bottom: 30, left: 44 }
  const iw = W - pad.left - pad.right
  const ih = H - pad.top - pad.bottom

  const x = (p) => pad.left + p * iw
  const y = (p) => pad.top + (1 - p) * ih

  const maxN = Math.max(...curve.map((b) => b.n))
  const radius = (n) => 3 + (maxN > 0 ? (n / maxN) * 6 : 0)

  const linePath = curve
    .map((b, i) => `${i ? 'L' : 'M'}${x(b.mean_predicted).toFixed(1)},${y(b.observed_freq).toFixed(1)}`)
    .join(' ')

  const ticks = [0, 0.25, 0.5, 0.75, 1]

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="reliability curve: predicted vs observed probability">
      {/* perfect-calibration diagonal */}
      <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--border)" strokeWidth="1" strokeDasharray="4,3" />
      {ticks.map((t) => (
        <g key={t}>
          <line x1={x(t)} x2={x(t)} y1={pad.top} y2={H - pad.bottom} stroke="var(--border)" strokeWidth="0.5" opacity="0.4" />
          <text x={x(t)} y={H - 10} fill="var(--muted)" fontSize="10" textAnchor="middle">{t.toFixed(2)}</text>
          <text x={pad.left - 6} y={y(t) + 3} fill="var(--muted)" fontSize="10" textAnchor="end">{t.toFixed(2)}</text>
        </g>
      ))}
      <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth="2" />
      {curve.map((b, i) => (
        <circle key={i} cx={x(b.mean_predicted)} cy={y(b.observed_freq)} r={radius(b.n)}
                fill="var(--accent)" opacity="0.85">
          <title>{`bin ${b.bin}: n=${b.n}, predicted ${b.mean_predicted.toFixed(3)}, observed ${b.observed_freq.toFixed(3)}`}</title>
        </circle>
      ))}
      <text x={W - pad.right} y={pad.top - 2} fill="var(--muted)" fontSize="9" textAnchor="end">predicted →</text>
      <text x={pad.left + 2} y={pad.top + 10} fill="var(--muted)" fontSize="9" textAnchor="start">↑ observed</text>
    </svg>
  )
}
