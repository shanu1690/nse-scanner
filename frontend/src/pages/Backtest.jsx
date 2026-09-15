import { useData, fmt, fmtPct } from '../api.js'

function HitRateBlock({ block, title }) {
  if (!block) return <div className="loading">Not enough signals to report {title.toLowerCase()}.</div>
  const hr = block.hit_rate
  return (
    <div>
      <h3 style={{ fontSize: 13, color: 'var(--muted)', margin: '0 0 8px' }}>{title}</h3>
      {!hr ? (
        <div className="loading">No signals cleared the score threshold in this window.</div>
      ) : (
        <>
          <div className="idea-fact"><span className="k">Hit rate</span><span className="v">{fmtPct(hr.hit_rate)}</span></div>
          <div className="idea-fact"><span className="k">Baseline</span><span className="v">{fmtPct(hr.baseline)}</span></div>
          <div className="idea-fact"><span className="k">Lift</span>
            <span className={`v ${hr.lift >= 0 ? 'up' : 'down'}`}>{hr.lift >= 0 ? '+' : ''}{fmtPct(hr.lift)}</span></div>
          <div className="idea-fact"><span className="k">95% CI</span>
            <span className="v">{hr.ci ? `[${fmtPct(hr.ci[0])}, ${fmtPct(hr.ci[1])}]` : 'n/a'}</span></div>
          <div className="idea-fact"><span className="k">Sample</span>
            <span className="v">{hr.n_signals} signals · {hr.n_symbols} symbols · {hr.n_months} months</span></div>
        </>
      )}
    </div>
  )
}

function TradeStats({ trades }) {
  if (!trades) return null
  return (
    <table>
      <thead>
        <tr><th>Cost assumption</th><th className="num">Trades</th><th className="num">Mean R</th>
          <th className="num">Win rate</th><th className="num">Profit factor</th>
          <th className="num">Max DD (R)</th><th className="num">Max losers</th></tr>
      </thead>
      <tbody>
        {[['cost_0_15pct', '0.15% round-trip'], ['cost_0_30pct', '0.30% (doubled, robustness)']].map(([key, label]) => {
          const s = trades[key]
          if (!s) return <tr key={key}><td>{label}</td><td colSpan={6} className="num">no simulated trades</td></tr>
          return (
            <tr key={key}>
              <td>{label}</td>
              <td className="num">{s.n_trades}</td>
              <td className="num">{s.mean_r >= 0 ? '+' : ''}{fmt(s.mean_r, 2)}</td>
              <td className="num">{fmtPct(s.win_rate)}</td>
              <td className="num">{s.profit_factor === null ? 'inf/n-a' : fmt(s.profit_factor, 2)}</td>
              <td className="num">{fmt(s.max_drawdown_r, 1)}</td>
              <td className="num">{s.max_consecutive_losers}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function Robustness({ r }) {
  if (!r) return null
  return (
    <ul className="reasons">
      <li>Excl. top contributor symbols ({r.excl_top_contributors?.symbols?.join(', ') || '-'}):
        {' '}lift {r.excl_top_contributors ? fmtPct(r.excl_top_contributors.lift) : 'n/a'} (full sample {fmtPct(r.base_lift)})</li>
      {r.excl_best_month && <li>Excl. best month ({r.excl_best_month.month}): lift {fmtPct(r.excl_best_month.lift)}</li>}
      {r.first_second_half && (
        <li>First half: {fmtPct(r.first_second_half.first)} · Second half: {fmtPct(r.first_second_half.second)}</li>
      )}
    </ul>
  )
}

export default function Backtest() {
  const { data, loading, error } = useData('backtest.json')

  if (loading) return <div className="loading">Loading…</div>
  if (error || !data) {
    return (
      <div className="card">
        <h2>Backtest</h2>
        <div className="loading">
          No backtest snapshot yet. This is generated on demand, not by the nightly build
          (a full walk-forward run takes 15-20+ minutes): run
          <div style={{ margin: '8px 0' }}><code>nse-scan backtest --export site/data/backtest.json</code></div>
          then rebuild the frontend, or just refresh once it's been generated.
        </div>
      </div>
    )
  }
  if (!data.ok) {
    return (
      <div className="card">
        <h2>Backtest</h2>
        <div className="loading">{data.message}</div>
      </div>
    )
  }

  const ho = data.held_out
  const shuffle = ho.label_shuffle

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>Walk-forward backtest</h2>
          <span className="pill">{data.style} · score ≥ {data.min_score} · {data.n_blocks} blocks</span>
        </div>
        <div className="legend">
          Generated {new Date(data.generated_at).toLocaleString()} · {data.universe_size} symbols ·
          coverage {fmtPct(data.coverage.ratio)} ({data.coverage.scored}/{data.coverage.universe})
        </div>
      </div>

      <div className="card">
        <div className="grid2">
          <HitRateBlock block={ho} title="Held-out (the number that matters)" />
          <HitRateBlock block={data.rolling} title="Rolling (context)" />
        </div>
      </div>

      <div className="card">
        <h2>Simulated trades (held-out)</h2>
        <TradeStats trades={ho.trades} />
      </div>

      <div className="card">
        <h2>Robustness checks (held-out)</h2>
        <Robustness r={ho.robustness} />
        {shuffle && (
          <div className="legend">
            Label-shuffle control: real lift {fmtPct(shuffle.real_lift)} vs shuffled {fmtPct(shuffle.shuffled_lift)}
            {' '}→ <span className={shuffle.verdict === 'PASS' ? 'verdict-pass' : 'verdict-review'}>{shuffle.verdict}</span>
          </div>
        )}
      </div>

      {data.factor_analysis && (
        <div className="card">
          <h2>Factor analysis (which sub-signals predict OOS moves)</h2>
          <table>
            <thead><tr><th>Factor</th><th className="num">n OOS rolls</th><th className="num">Mean diff (hi-lo)</th></tr></thead>
            <tbody>
              {Object.entries(data.factor_analysis).map(([f, v]) => (
                <tr key={f}>
                  <td>{f}</td>
                  <td className="num">{v.n_oos_rolls}</td>
                  <td className="num">{v.mean_diff_pct === null ? 'n/a' : `${v.mean_diff_pct >= 0 ? '+' : ''}${fmt(v.mean_diff_pct, 2)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
