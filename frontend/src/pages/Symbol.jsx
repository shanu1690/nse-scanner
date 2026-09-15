import { useParams, Link } from 'react-router-dom'
import { useData, fmt } from '../api.js'
import CandleChart from '../components/CandleChart.jsx'

export default function Symbol() {
  const { symbol } = useParams()
  const price = useData(`prices/${symbol}.json`)
  const chain = useData(`chains/${symbol}.json`)

  if (price.loading) return <div className="loading">Loading…</div>

  const p = price.data
  if (!p || price.error) {
    return (
      <div>
        <Link className="back" to="/">← back</Link>
        <h1>{symbol} <small>no data</small></h1>
        <div className="error">No price series for {symbol}. It may not be in today's picks.</div>
      </div>
    )
  }

  const last = p.last || {}
  const c = chain.data

  return (
    <div>
      <Link className="back" to="/">← back</Link>
      <h1>
        {symbol}{' '}
        <small>
          as of {last.date} · close {fmt(last.close)} · RSI {last.rsi14} ·{' '}
          ROC5 {fmt(last.roc5)}% / ROC20 {fmt(last.roc20)}%
        </small>
      </h1>

      <div className="card">
        <h2>Price &amp; indicators</h2>
        <div className="grid2">
          <CandleChart series={p.series} levels={p.levels} />
          <div>
            <table>
              <tbody>
                <tr><td>Close</td><td className="num">{fmt(last.close)}</td></tr>
                <tr><td>EMA 21 / 50 / 200</td><td className="num">{fmt(last.ema21)} / {fmt(last.ema50)} / {fmt(last.ema200)}</td></tr>
                <tr><td>RSI 14</td><td className="num">{fmt(last.rsi14)}</td></tr>
                <tr><td>52w high / low</td><td className="num">{fmt(last.high_52w)} / {fmt(last.low_52w)}</td></tr>
                <tr><td>ROC 5d / 20d</td><td className="num">{fmt(last.roc5)}% / {fmt(last.roc20)}%</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {c && c.rows && c.rows.length > 0 && (
        <div className="card">
          <h2>Option chain · {c.expiry} · spot {fmt(c.spot)}</h2>
          <table>
            <thead>
              <tr>
                <th className="num">Strike</th>
                <th className="num">CE OI</th>
                <th className="num">CE IV</th>
                <th className="num">CE Prem</th>
                <th className="num">PE Prem</th>
                <th className="num">PE IV</th>
                <th className="num">PE OI</th>
              </tr>
            </thead>
            <tbody>
              {c.rows.slice().reverse().map((r) => (
                <tr key={r.strike}>
                  <td className="num"><b>{fmt(r.strike)}</b></td>
                  <td className="num">{fmt(r.ce.oi, 0)}</td>
                  <td className="num">{r.ce.iv ? `${r.ce.iv.toFixed(0)}%` : '-'}</td>
                  <td className="num">{fmt(r.ce.prem)}</td>
                  <td className="num">{fmt(r.pe.prem)}</td>
                  <td className="num">{r.pe.iv ? `${r.pe.iv.toFixed(0)}%` : '-'}</td>
                  <td className="num">{fmt(r.pe.oi, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
