import { Link, NavLink, Route, Routes } from 'react-router-dom'
import Today from './pages/Today.jsx'
import Options from './pages/Options.jsx'
import Backtest from './pages/Backtest.jsx'
import Journal from './pages/Journal.jsx'
import Symbol from './pages/Symbol.jsx'
import { useData, useTheme, manifestAgeMinutes, STALE_AFTER_MINUTES } from './api.js'

function StaleBadge({ manifest }) {
  if (!manifest) return null
  const ageMin = manifestAgeMinutes(manifest)
  if (ageMin === null) return null
  const stale = ageMin > STALE_AFTER_MINUTES
  const label = ageMin < 60 ? `${Math.round(ageMin)}m ago` : `${(ageMin / 60).toFixed(1)}h ago`
  return (
    <span className={`stale-badge ${stale ? 'stale' : 'fresh'}`} title={`Bundle generated ${manifest.generated}`}>
      <span className="dot" />
      {stale ? 'stale' : 'live'} · data as of {label}
    </span>
  )
}

export default function App() {
  const { data: manifest } = useData('manifest.json')
  const [theme, toggleTheme] = useTheme()

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">NSE Scanner</Link>
        <nav>
          <NavLink to="/" end>Today</NavLink>
          <NavLink to="/options">Options</NavLink>
          <NavLink to="/backtest">Backtest</NavLink>
          <NavLink to="/journal">Journal</NavLink>
        </nav>
        <span className="meta">
          <StaleBadge manifest={manifest} />
          {manifest ? (
            <>
              {manifest.date} &middot; {manifest.universe_size} symbols &middot;{' '}
              {manifest.provider}
            </>
          ) : (
            'loading…'
          )}
          <button className="theme-toggle" onClick={toggleTheme}
                  aria-label="Toggle light/dark theme" title="Toggle light/dark theme">
            {theme === 'dark' ? '☀️' : '🌙'}
          </button>
        </span>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/options" element={<Options />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/scorecard" element={<Journal />} />
          <Route path="/symbol/:symbol" element={<Symbol />} />
        </Routes>
      </main>

      <footer>
        Data refreshes via the nightly scan. Not investment advice.
        DISCLAIMER.md has the full text.
      </footer>
    </div>
  )
}
