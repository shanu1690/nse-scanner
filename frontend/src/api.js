import { useEffect, useState } from 'react'

// All data lives as static JSON beside the app (./data/...). Relative base
// keeps it working at any GitHub Pages path prefix.
export function useData(path) {
  const [state, setState] = useState({ data: null, loading: true, error: null })
  useEffect(() => {
    let alive = true
    setState({ data: null, loading: true, error: null })
    fetch(`./data/${path}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
        return r.json()
      })
      .then((data) => alive && setState({ data, loading: false, error: null }))
      .catch((err) => alive && setState({ data: null, loading: false, error: String(err) }))
    return () => { alive = false }
  }, [path])
  return state
}

export function fmt(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-'
  if (typeof n === 'string') return n
  return n.toLocaleString('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtMoney(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-'
  return '₹' + fmt(n, 0)
}

export function fmtPct(n, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-'
  return `${(n * 100).toFixed(digits)}%`
}

// ------------------------------------------------------------------ theme
const THEME_KEY = 'nse-scanner-theme'

export function useTheme() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem(THEME_KEY) || 'dark' } catch { return 'dark' }
  })
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem(THEME_KEY, theme) } catch { /* private-window etc -- fine to no-op */ }
  }, [theme])
  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  return [theme, toggle]
}

// --------------------------------------------------------------- staleness
// No live feed exists yet (Section 4's backend is a separate, unbuilt
// phase) -- this is a periodically-regenerated static bundle, so
// "staleness" means "how long since the last successful nse-scan site
// run", read from manifest.json's own generated timestamp.
export const STALE_AFTER_MINUTES = 60 * 20 // ~20h: comfortably past one missed nightly run

export function manifestAgeMinutes(manifest) {
  if (!manifest || !manifest.generated) return null
  const generated = new Date(manifest.generated.replace(' ', 'T'))
  if (Number.isNaN(generated.getTime())) return null
  return (Date.now() - generated.getTime()) / 60000
}

// -------------------------------------------------------------- URL state
// Encodes a plain {key: value} filter-state object into the URL's query
// string (rule: "filter state encoded in the URL so a view can be
// bookmarked and shared") without pulling in a router-state library --
// falsy/default values are omitted so the URL stays clean.
export function readParams(search) {
  const out = {}
  new URLSearchParams(search).forEach((v, k) => { out[k] = v })
  return out
}

export function writeParams(params) {
  const usp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '' || v === false) continue
    usp.set(k, String(v))
  }
  const qs = usp.toString()
  const url = qs ? `${window.location.pathname}?${qs}${window.location.hash}` : window.location.pathname + window.location.hash
  window.history.replaceState(null, '', url)
}

// -------------------------------------------------------------------- csv
export function downloadCsv(filename, headers, rows) {
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [headers.map(esc).join(','), ...rows.map((r) => r.map(esc).join(','))]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
