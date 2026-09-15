import { Link } from 'react-router-dom'
import { useData, fmtMoney } from '../api.js'
import DeliveryTable from '../components/DeliveryTable.jsx'

function OptionsSummaryCard() {
  const { data, loading, error } = useData('options.json')
  if (loading) return <div className="card"><div className="loading">Loading option ideas…</div></div>
  if (error) return null
  const picks = data?.picks || []
  return (
    <div className="card">
      <div className="card-head">
        <h2>Option ideas</h2>
        <Link to="/options" className="back">See all →</Link>
      </div>
      {!picks.length ? (
        <div className="loading">No qualifying option trade today.</div>
      ) : (
        <table>
          <thead>
            <tr><th>Symbol</th><th>Dir</th><th>Strategy</th><th className="num">Cost</th>
              <th className="num">Probability</th></tr>
          </thead>
          <tbody>
            {picks.slice(0, 5).map((p) => (
              <tr key={p.symbol}>
                <td><Link className="sym" to={`/symbol/${p.symbol}`}>{p.symbol}</Link></td>
                <td><span className={`badge ${p.direction === 'CE' ? 'ce' : 'pe'}`}>{p.direction}</span></td>
                <td>{p.strategy === 'debit_spread' ? 'Spread' : 'Long'}</td>
                <td className="num">{fmtMoney(p.cost)}</td>
                <td className="num">{p.probability === null ? '-' : `${(p.probability * 100).toFixed(0)}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function Today() {
  const delivery = useData('delivery.json')

  if (delivery.loading) return <div className="loading">Loading…</div>
  if (delivery.error) {
    return (
      <div className="error">
        No data yet. Run <code>nse-scan site</code> (or the nightly GitHub
        Action) to generate it.
        <div>{delivery.error}</div>
      </div>
    )
  }

  return (
    <>
      <div className="card">
        <h2>Delivery / Momentum picks</h2>
        <DeliveryTable picks={delivery.data.picks} style={delivery.data.style} />
      </div>
      <OptionsSummaryCard />
    </>
  )
}
