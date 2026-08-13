import { Link } from 'react-router-dom'
import { fmt, fmtMoney } from '../api.js'

function reasonsBlock(p) {
  if (!p.reasons || !p.reasons.length) return null
  return (
    <ul className="reasons">
      {p.reasons.map((r, i) => <li key={i}>{r}</li>)}
    </ul>
  )
}

export function DeliveryTable({ picks, style }) {
  if (!picks.length) return <div className="loading">No delivery signals today.</div>
  return (
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th className="num">Score</th>
          <th className="num">Entry</th>
          <th className="num">Stop</th>
          <th className="num">T1</th>
          <th className="num">T2</th>
          <th>Style</th>
          <th>Why</th>
        </tr>
      </thead>
      <tbody>
        {picks.map((p) => (
          <tr key={p.symbol}>
            <td><Link className="sym" to={`/symbol/${p.symbol}`}>{p.symbol}</Link></td>
            <td className="num"><span className="badge score">{p.score.toFixed(0)}</span></td>
            <td className="num">{fmt(p.entry)}</td>
            <td className="num">{fmt(p.stop)}</td>
            <td className="num">{fmt(p.target1)}</td>
            <td className="num">{fmt(p.target2)}</td>
            <td>{style || 'momentum'}</td>
            <td>{reasonsBlock(p)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function OptionsTable({ picks }) {
  if (!picks.length) return <div className="loading">No option picks today.</div>
  return (
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Dir</th>
          <th className="num">Score</th>
          <th className="num">Spot</th>
          <th className="num">Strike</th>
          <th className="num">Premium</th>
          <th className="num">Breakeven</th>
          <th>Expiry</th>
          <th className="num">Lot</th>
          <th className="num">Amount</th>
          <th className="num">PCR</th>
          <th className="num">ATM IV</th>
          <th className="num">Max Pain</th>
        </tr>
      </thead>
      <tbody>
        {picks.map((p) => (
          <tr key={p.symbol}>
            <td><Link className="sym" to={`/symbol/${p.symbol}`}>{p.symbol}</Link></td>
            <td>
              <span className={`badge ${p.direction === 'CE' ? 'ce' : p.direction === 'PE' ? 'pe' : 'flat'}`}>
                {p.direction}
              </span>
            </td>
            <td className="num">{p.score.toFixed(0)}</td>
            <td className="num">{fmt(p.spot, 0)}</td>
            <td className="num">{fmt(p.strike)}</td>
            <td className="num">{fmt(p.premium)}</td>
            <td className="num">{fmt(p.breakeven)}</td>
            <td>{p.expiry}</td>
            <td className="num">{p.lot_size ?? '-'}</td>
            <td className="num">{fmtMoney(p.amount_per_lot)}</td>
            <td className="num">{p.chain?.pcr ?? '-'}</td>
            <td className="num">{p.chain?.atm_iv ?? '-'}</td>
            <td className="num">{fmt(p.chain?.max_pain ?? null, 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
