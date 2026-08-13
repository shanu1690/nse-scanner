import { useData, fmt } from '../api.js'

function StatusCell({ status }) {
  const cls =
    /hit/i.test(status) ? 'up' :
    /stop|closed|expired/i.test(status) ? 'down' : ''
  return <td className={cls}>{status}</td>
}

export default function Scorecard() {
  const { data, loading, error } = useData('scorecard.json')
  if (loading) return <div className="loading">Loading…</div>
  if (error) return <div className="error">{error}</div>
  const delRows = data.del_rows || []
  const optRows = data.opt_rows || []
  if (!delRows.length && !optRows.length) {
    return <div className="loading">No tracked picks yet.</div>
  }
  return (
    <>
      <h1>Scorecard <small>honesty ledger — how past picks actually did</small></h1>
      {delRows.length > 0 && (
        <div className="card">
          <h2>Delivery picks</h2>
          <table>
            <thead>
              <tr>{data.del_headers.map((h, i) => <th key={i} className={i > 0 ? 'num' : ''}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {delRows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => j === 1
                    ? <td key={j}><b>{c}</b></td>
                    : j === 8
                      ? <StatusCell key={j} status={c} />
                      : <td key={j} className={j > 0 ? 'num' : ''}>{fmt(c, 2)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {optRows.length > 0 && (
        <div className="card">
          <h2>Option picks</h2>
          <table>
            <thead>
              <tr>{data.opt_headers.map((h, i) => <th key={i} className={i > 0 ? 'num' : ''}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {optRows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => j === 1
                    ? <td key={j}><b>{c}</b></td>
                    : j === 8
                      ? <StatusCell key={j} status={c} />
                      : <td key={j} className={j > 0 ? 'num' : ''}>{fmt(c, 2)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
