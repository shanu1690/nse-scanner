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
