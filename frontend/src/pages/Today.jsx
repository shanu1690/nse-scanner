import { useData } from '../api.js'
import { DeliveryTable, OptionsTable } from '../components/tables.jsx'

export default function Today() {
  const delivery = useData('delivery.json')
  const options = useData('options.json')

  if (delivery.loading || options.loading) return <div className="loading">Loading…</div>
  if (delivery.error || options.error) {
    return (
      <div className="error">
        No data yet. Run <code>nse-scan site</code> (or the nightly GitHub
        Action) to generate it.
        <div>{delivery.error || options.error}</div>
      </div>
    )
  }

  return (
    <>
      <div className="card">
        <h2>Delivery / Momentum picks</h2>
        <DeliveryTable picks={delivery.data.picks} style={delivery.data.style} />
      </div>
      <div className="card">
        <h2>Option picks (nearest expiry)</h2>
        <OptionsTable picks={options.data.picks} />
      </div>
    </>
  )
}
