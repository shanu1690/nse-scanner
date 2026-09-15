// Dependency-free SVG payoff diagram -- matches the project's existing
// no-extra-dependency chart style. Section 5.2 rule 6: "a payoff diagram
// with breakeven marked and max loss shaded."
export default function PayoffChart({ points, breakeven, spot, height = 220 }) {
  if (!points || points.length < 2) return <div className="loading">No payoff data.</div>

  const W = 500
  const H = height
  const pad = { top: 14, right: 14, bottom: 26, left: 54 }
  const iw = W - pad.left - pad.right
  const ih = H - pad.top - pad.bottom

  const prices = points.map((p) => p.price)
  const pnls = points.map((p) => p.pnl)
  const minPrice = Math.min(...prices)
  const maxPrice = Math.max(...prices)
  let minPnl = Math.min(0, ...pnls)
  let maxPnl = Math.max(0, ...pnls)
  const pnlRange = maxPnl - minPnl || 1
  minPnl -= pnlRange * 0.08
  maxPnl += pnlRange * 0.08

  const x = (price) => pad.left + ((price - minPrice) / (maxPrice - minPrice || 1)) * iw
  const y = (pnl) => pad.top + (1 - (pnl - minPnl) / (maxPnl - minPnl)) * ih

  const linePath = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.price).toFixed(1)},${y(p.pnl).toFixed(1)}`).join(' ')
  const zeroY = y(0)

  // Shade profit (above zero) green, loss (below zero) red, split at the
  // curve itself via two clipped area paths.
  const areaAbove = `${linePath} L${x(points[points.length - 1].price).toFixed(1)},${zeroY} L${x(points[0].price).toFixed(1)},${zeroY} Z`

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="option payoff diagram">
      {/* zero P&L reference line */}
      <line x1={pad.left} x2={W - pad.right} y1={zeroY} y2={zeroY} stroke="var(--border)" strokeWidth="1" />
      <path d={areaAbove} fill="var(--accent)" opacity="0.08" />
      <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth="2" />

      {spot !== undefined && spot !== null && (
        <line x1={x(spot)} x2={x(spot)} y1={pad.top} y2={H - pad.bottom}
              stroke="var(--muted)" strokeWidth="1" strokeDasharray="3,3" />
      )}
      {breakeven !== undefined && breakeven !== null && breakeven >= minPrice && breakeven <= maxPrice && (
        <>
          <line x1={x(breakeven)} x2={x(breakeven)} y1={pad.top} y2={H - pad.bottom}
                stroke="var(--up)" strokeWidth="1" strokeDasharray="4,2" />
          <text x={x(breakeven)} y={pad.top - 2} fill="var(--up)" fontSize="10" textAnchor="middle">BE</text>
        </>
      )}

      {[minPrice, (minPrice + maxPrice) / 2, maxPrice].map((p, i) => (
        <text key={i} x={x(p)} y={H - 6} fill="var(--muted)" fontSize="10" textAnchor="middle">
          {p.toFixed(0)}
        </text>
      ))}
      {[minPnl, 0, maxPnl].map((v, i) => (
        <text key={i} x={pad.left - 6} y={y(v) + 3} fill="var(--muted)" fontSize="10" textAnchor="end">
          {v >= 1000 || v <= -1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0)}
        </text>
      ))}
      <text x={W - pad.right} y={H - 6} fill="var(--muted)" fontSize="9" textAnchor="end">underlying price</text>
    </svg>
  )
}
