import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useData, fmt, fmtMoney, fmtPct, downloadCsv } from '../api.js'
import PayoffChart from '../components/PayoffChart.jsx'

function IdeaPanel({ pick }) {
  return (
    <div className="payoff-wrap">
      <div>
        <PayoffChart points={pick.payoff} breakeven={pick.breakeven} spot={pick.spot} />
      </div>
      <div>
        <div className="idea-fact"><span className="k">Strategy</span><span className="v">{pick.strategy === 'debit_spread' ? 'Debit spread' : `Long ${pick.direction}`}</span></div>
        <div className="idea-fact"><span className="k">Legs</span><span className="v">
          {pick.legs.map((l, i) => `${l.action} ${l.side} ${l.strike}`).join(' / ')}
        </span></div>
        <div className="idea-fact"><span className="k">Total cost</span><span className="v">{fmtMoney(pick.cost)}</span></div>
        <div className="idea-fact"><span className="k">Max possible loss</span>
          <span className="v max-loss-warning">{fmtMoney(pick.max_loss)} (100% of amount paid)</span></div>
        <div className="idea-fact"><span className="k">Max possible profit</span>
          <span className="v">{pick.max_profit === null ? 'Unlimited' : fmtMoney(pick.max_profit)}</span></div>
        <div className="idea-fact"><span className="k">Breakeven</span><span className="v">{fmt(pick.breakeven)}</span></div>
        <div className="idea-fact"><span className="k">Modeled probability</span>
          <span className="v">{pick.probability === null ? 'n/a' : fmtPct(pick.probability)}</span></div>
        <div className="idea-fact"><span className="k">Days to expiry</span><span className="v">{pick.dte}</span></div>
        <div className="idea-fact"><span className="k">Lot size</span><span className="v">{pick.lot_size}</span></div>
        <div className="thesis">{pick.thesis}</div>
      </div>
    </div>
  )
}

export default function Options() {
  const { data, loading, error } = useData('options.json')
  const [expanded, setExpanded] = useState(null)
  const [maxCost, setMaxCost] = useState('')

  const picks = data?.picks || []
  const filtered = useMemo(
    () => picks.filter((p) => maxCost === '' || p.cost <= Number(maxCost)),
    [picks, maxCost]
  )

  if (loading) return <div className="loading">Loading…</div>
  if (error) {
    return (
      <div className="error">
        No options data yet. Run <code>nse-scan site</code> to generate it.
        <div>{error}</div>
      </div>
    )
  }

  function exportCsv() {
    const headers = ['Symbol', 'Direction', 'Strategy', 'Cost', 'Max Loss', 'Max Profit', 'Breakeven', 'Probability', 'DTE']
    const rows = filtered.map((p) => [p.symbol, p.direction, p.strategy, p.cost, p.max_loss,
      p.max_profit ?? 'unlimited', p.breakeven, p.probability ?? '', p.dte])
    downloadCsv(`option-ideas-${new Date().toISOString().slice(0, 10)}.csv`, headers, rows)
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>Option ideas (Rs 10,000 budget cap, every idea already fits)</h2>
      </div>
      {!picks.length ? (
        <div className="loading">No qualifying option trade today. This is a valid, expected outcome
          (see PROJECT_BRIEF.md Section 5, rule 1) -- not every day produces a trade that clears the
          budget and quality bar.</div>
      ) : (
        <>
          <div className="toolbar">
            <span className="range">
              Max cost <input type="number" value={maxCost} onChange={(e) => setMaxCost(e.target.value)}
                              placeholder="10000" />
            </span>
            <span className="pill">sorted by modeled probability</span>
            <span className="spacer" />
            <span className="result-count">{filtered.length} of {picks.length}</span>
            <button onClick={exportCsv}>Export CSV</button>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Symbol</th><th>Dir</th><th>Strategy</th>
                  <th className="num">Cost</th><th className="num">Max Loss</th>
                  <th className="num">Breakeven</th><th className="num">Probability</th>
                  <th className="num">DTE</th><th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p) => (
                  <Fragment key={p.symbol}>
                    <tr style={{ cursor: 'pointer' }}
                        onClick={() => setExpanded(expanded === p.symbol ? null : p.symbol)}>
                      <td><Link className="sym" to={`/symbol/${p.symbol}`} onClick={(e) => e.stopPropagation()}>{p.symbol}</Link></td>
                      <td><span className={`badge ${p.direction === 'CE' ? 'ce' : 'pe'}`}>{p.direction}</span></td>
                      <td><span className={`badge ${p.strategy === 'debit_spread' ? 'spread' : 'long'}`}>
                        {p.strategy === 'debit_spread' ? 'Spread' : 'Long'}</span></td>
                      <td className="num">{fmtMoney(p.cost)}</td>
                      <td className="num">{fmtMoney(p.max_loss)}</td>
                      <td className="num">{fmt(p.breakeven)}</td>
                      <td className="num">{p.probability === null ? '-' : fmtPct(p.probability)}</td>
                      <td className="num">{p.dte}</td>
                      <td className="num">{expanded === p.symbol ? '▲' : '▼'}</td>
                    </tr>
                    {expanded === p.symbol && (
                      <tr>
                        <td colSpan={9} className="panel-cell"><IdeaPanel pick={p} /></td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
