import { Link, NavLink, Route, Routes } from 'react-router-dom'
import Today from './pages/Today.jsx'
import Scorecard from './pages/Scorecard.jsx'
import Symbol from './pages/Symbol.jsx'
import { useData } from './api.js'

export default function App() {
  const { data: manifest } = useData('manifest.json')

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">NSE Scanner</Link>
        <nav>
          <NavLink to="/" end>Today</NavLink>
          <NavLink to="/scorecard">Scorecard</NavLink>
        </nav>
        <span className="meta">
          {manifest ? (
            <>
              {manifest.date} &middot; {manifest.universe_size} symbols &middot;{' '}
              {manifest.provider} &middot; built in {manifest.build_seconds}s
            </>
          ) : (
            'loading…'
          )}
        </span>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/scorecard" element={<Scorecard />} />
          <Route path="/symbol/:symbol" element={<Symbol />} />
        </Routes>
      </main>

      <footer>
        Data refreshes via the nightly scan. Not investment advice.
      </footer>
    </div>
  )
}
