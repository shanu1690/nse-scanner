import { useData, fmt } from '../api.js'

const OPEN_STATUS = 'OPEN'

function StatusCell({ status }) {
  const cls =
    /hit|profit/i.test(status) ? 'up' :
    /stop|loss/i.test(status) ? 'down' : ''
  return <td className={cls}>{status}</td>
}

function JournalTable({ headers, rows, statusIdx }) {
  if (!rows.length) return null
  return (
    <table>
      <thead>
        <tr>{headers.map((h, i) => <th key={i} className={i > 0 ? 'num' : ''}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {r.map((c, j) => j === 1
              ? <td key={j}><b>{c}</b></td>
              : j === statusIdx
                ? <StatusCell key={j} status={c} />
                : <td key={j} className={j > 0 ? 'num' : ''}>{fmt(c, 2)}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function Journal() {
  const { data, loading, error } = useData('scorecard.json')
  if (loading) return <div className="loading">Loading…</div>
  if (error) return <div className="error">{error}</div>
  const delRows = data.del_rows || []
  const optRows = data.opt_rows || []
  if (!delRows.length && !optRows.length) {
    return <div className="loading">No tracked picks yet.</div>
  }

  const delStatusIdx = data.del_headers.indexOf('STATUS')
  const optStatusIdx = data.opt_headers.indexOf('STATUS')
  const delOpen = delRows.filter((r) => r[delStatusIdx] === OPEN_STATUS)
  const delClosed = delRows.filter((r) => r[delStatusIdx] !== OPEN_STATUS)
  const optOpen = optRows.filter((r) => r[optStatusIdx] === OPEN_STATUS)
  const optClosed = optRows.filter((r) => r[optStatusIdx] !== OPEN_STATUS)

  return (
    <>
      <h1>Journal <small>honesty ledger — how past picks actually did, published whether it looks good or bad</small></h1>

      {(delOpen.length > 0 || optOpen.length > 0) && (
        <div className="card">
          <h2>Open ideas — live P&amp;L</h2>
          {delOpen.length > 0 && <>
            <h3 style={{ fontSize: 13, color: 'var(--muted)' }}>Delivery</h3>
            <JournalTable headers={data.del_headers} rows={delOpen} statusIdx={delStatusIdx} />
          </>}
          {optOpen.length > 0 && <>
            <h3 style={{ fontSize: 13, color: 'var(--muted)', marginTop: 12 }}>Options</h3>
            <JournalTable headers={data.opt_headers} rows={optOpen} statusIdx={optStatusIdx} />
          </>}
        </div>
      )}

      {(delClosed.length > 0 || optClosed.length > 0) && (
        <div className="card">
          <h2>Closed ideas — outcome</h2>
          {delClosed.length > 0 && <>
            <h3 style={{ fontSize: 13, color: 'var(--muted)' }}>Delivery</h3>
            <JournalTable headers={data.del_headers} rows={delClosed} statusIdx={delStatusIdx} />
          </>}
          {optClosed.length > 0 && <>
            <h3 style={{ fontSize: 13, color: 'var(--muted)', marginTop: 12 }}>Options</h3>
            <JournalTable headers={data.opt_headers} rows={optClosed} statusIdx={optStatusIdx} />
          </>}
        </div>
      )}
    </>
  )
}
