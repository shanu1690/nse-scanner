import { useMemo, useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  useReactTable, getCoreRowModel, getSortedRowModel, getFilteredRowModel,
  flexRender,
} from '@tanstack/react-table'
import { fmt, fmtMoney, fmtPct, readParams, writeParams, downloadCsv } from '../api.js'

const ALL_COLUMNS = [
  { id: 'symbol', label: 'Symbol', alwaysOn: true },
  { id: 'score', label: 'Score' },
  { id: 'entry', label: 'Entry' },
  { id: 'stop', label: 'Stop' },
  { id: 'target1', label: 'T1' },
  { id: 'target2', label: 'T2' },
  { id: 'position_size', label: 'Shares' },
  { id: 'rupee_risk', label: 'Risk (₹)' },
  { id: 'risk_pct_of_capital', label: 'Risk %' },
  { id: 'reward_risk', label: 'R:R' },
  { id: 'sector', label: 'Sector' },
  { id: 'reasons', label: 'Why' },
]
const DEFAULT_HIDDEN = new Set(['risk_pct_of_capital'])

const PRESETS_KEY = 'nse-scanner-delivery-presets'

function loadPresets() {
  try { return JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}') } catch { return {} }
}
function savePresets(p) {
  try { localStorage.setItem(PRESETS_KEY, JSON.stringify(p)) } catch { /* ignore */ }
}

function reasonsCell(reasons) {
  if (!reasons || !reasons.length) return null
  return (
    <ul className="reasons">
      {reasons.map((r, i) => <li key={i}>{r}</li>)}
    </ul>
  )
}

function parseSortParam(raw) {
  if (!raw) return [{ id: 'score', desc: true }]
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : [{ id: 'score', desc: true }]
  } catch {
    return [{ id: 'score', desc: true }] // a hand-edited/stale URL shouldn't crash the page
  }
}

export default function DeliveryTable({ picks, style }) {
  const initial = readParams(window.location.search)
  const [search, setSearch] = useState(initial.q || '')
  const [minScore, setMinScore] = useState(initial.minScore || '')
  const [maxScore, setMaxScore] = useState(initial.maxScore || '')
  const [minPrice, setMinPrice] = useState(initial.minPrice || '')
  const [maxPrice, setMaxPrice] = useState(initial.maxPrice || '')
  const [sector, setSector] = useState(initial.sector || '')
  const [sorting, setSorting] = useState(() => parseSortParam(initial.sort))
  const [hiddenCols, setHiddenCols] = useState(() => {
    if (initial.hide) return new Set(initial.hide.split(','))
    return new Set(DEFAULT_HIDDEN)
  })
  const [colMenuOpen, setColMenuOpen] = useState(false)
  const [presets, setPresets] = useState(loadPresets)

  useEffect(() => {
    writeParams({
      q: search, minScore, maxScore, minPrice, maxPrice, sector,
      sort: sorting.length ? JSON.stringify(sorting) : '',
      hide: [...hiddenCols].join(','),
    })
  }, [search, minScore, maxScore, minPrice, maxPrice, sector, sorting, hiddenCols])

  const sectors = useMemo(
    () => [...new Set(picks.map((p) => p.sector || 'Unknown'))].sort(),
    [picks]
  )

  const filtered = useMemo(() => {
    return picks.filter((p) => {
      if (search && !p.symbol.toLowerCase().includes(search.toLowerCase())) return false
      if (minScore !== '' && p.score < Number(minScore)) return false
      if (maxScore !== '' && p.score > Number(maxScore)) return false
      if (minPrice !== '' && p.entry < Number(minPrice)) return false
      if (maxPrice !== '' && p.entry > Number(maxPrice)) return false
      if (sector && (p.sector || 'Unknown') !== sector) return false
      return true
    })
  }, [picks, search, minScore, maxScore, minPrice, maxPrice, sector])

  const columns = useMemo(() => [
    {
      accessorKey: 'symbol', header: 'Symbol',
      cell: (c) => <Link className="sym" to={`/symbol/${c.getValue()}`}>{c.getValue()}</Link>,
    },
    {
      accessorKey: 'score', header: 'Score',
      cell: (c) => <span className="badge score">{c.getValue().toFixed(0)}</span>,
    },
    { accessorKey: 'entry', header: 'Entry', cell: (c) => fmt(c.getValue()) },
    { accessorKey: 'stop', header: 'Stop', cell: (c) => fmt(c.getValue()) },
    { accessorKey: 'target1', header: 'T1', cell: (c) => fmt(c.getValue()) },
    { accessorKey: 'target2', header: 'T2', cell: (c) => fmt(c.getValue()) },
    { accessorKey: 'position_size', header: 'Shares', cell: (c) => c.getValue() ?? '-' },
    { accessorKey: 'rupee_risk', header: 'Risk (₹)', cell: (c) => fmtMoney(c.getValue()) },
    { accessorKey: 'risk_pct_of_capital', header: 'Risk %', cell: (c) => fmt(c.getValue(), 2) },
    { accessorKey: 'reward_risk', header: 'R:R', cell: (c) => fmt(c.getValue(), 1) },
    { accessorKey: 'sector', header: 'Sector', cell: (c) => c.getValue() || <span className="pill">unknown</span> },
    {
      accessorKey: 'reasons', header: 'Why', enableSorting: false,
      cell: (c) => reasonsCell(c.getValue()),
    },
  ], [])

  const visibleColumns = useMemo(
    () => columns.filter((c) => !hiddenCols.has(c.accessorKey) || ALL_COLUMNS.find((x) => x.id === c.accessorKey)?.alwaysOn),
    [columns, hiddenCols]
  )

  const table = useReactTable({
    data: filtered,
    columns: visibleColumns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    isMultiSortEvent: (e) => e.shiftKey,
  })

  function toggleCol(id) {
    setHiddenCols((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function exportCsv() {
    const cols = visibleColumns.filter((c) => c.accessorKey !== 'reasons')
    const headers = cols.map((c) => c.header)
    const rows = table.getRowModel().rows.map((r) => cols.map((c) => r.original[c.accessorKey]))
    downloadCsv(`delivery-picks-${new Date().toISOString().slice(0, 10)}.csv`, headers, rows)
  }

  function savePreset() {
    const name = window.prompt('Preset name?')
    if (!name) return
    const next = { ...presets, [name]: { search, minScore, maxScore, minPrice, maxPrice, sector } }
    setPresets(next)
    savePresets(next)
  }

  function applyPreset(name) {
    const p = presets[name]
    if (!p) return
    setSearch(p.search || ''); setMinScore(p.minScore || ''); setMaxScore(p.maxScore || '')
    setMinPrice(p.minPrice || ''); setMaxPrice(p.maxPrice || ''); setSector(p.sector || '')
  }

  if (!picks.length) return <div className="loading">No delivery signals today.</div>

  return (
    <>
      <div className="toolbar">
        <input className="search" type="search" placeholder="Search symbol…"
               value={search} onChange={(e) => setSearch(e.target.value)} />
        <span className="range">
          Score <input type="number" value={minScore} onChange={(e) => setMinScore(e.target.value)} placeholder="min" />
          – <input type="number" value={maxScore} onChange={(e) => setMaxScore(e.target.value)} placeholder="max" />
        </span>
        <span className="range">
          Price <input type="number" value={minPrice} onChange={(e) => setMinPrice(e.target.value)} placeholder="min" />
          – <input type="number" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} placeholder="max" />
        </span>
        <select value={sector} onChange={(e) => setSector(e.target.value)}>
          <option value="">All sectors</option>
          {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="pill">{style || 'momentum'} style</span>
        <span className="spacer" />
        <span className="result-count">{filtered.length} of {picks.length}</span>
        <div className="col-menu">
          <button onClick={() => setColMenuOpen((v) => !v)}>Columns</button>
          {colMenuOpen && (
            <div className="col-menu-panel">
              {ALL_COLUMNS.filter((c) => !c.alwaysOn).map((c) => (
                <label key={c.id}>
                  <input type="checkbox" checked={!hiddenCols.has(c.id)} onChange={() => toggleCol(c.id)} />
                  {c.label}
                </label>
              ))}
            </div>
          )}
        </div>
        <button onClick={exportCsv}>Export CSV</button>
        <button onClick={savePreset}>Save view</button>
        {Object.keys(presets).length > 0 && (
          <select onChange={(e) => e.target.value && applyPreset(e.target.value)} defaultValue="">
            <option value="" disabled>Load view…</option>
            {Object.keys(presets).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        )}
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const sortIdx = sorting.findIndex((s) => s.id === h.column.id)
                  const dir = h.column.getIsSorted()
                  return (
                    <th key={h.id}
                        className={h.column.getCanSort() ? 'sortable num' : 'num'}
                        onClick={h.column.getToggleSortingHandler()}
                        title={h.column.getCanSort() ? 'Click to sort, shift-click to add a sort column' : undefined}>
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {dir && <span className="sort-arrow">{dir === 'asc' ? '▲' : '▼'}</span>}
                      {sorting.length > 1 && sortIdx > -1 && <span className="sort-order">{sortIdx + 1}</span>}
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className={cell.column.id === 'symbol' || cell.column.id === 'reasons' ? '' : 'num'}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length === 0 && <div className="loading">No picks match these filters.</div>}
      <div className="legend">
        Filters shown are limited to fields this pipeline actually produces today
        (score, entry price, sector when a verified mapping exists). Volume/market-cap/
        earnings-blackout/watchlist filters aren't wired to real data yet.
      </div>
    </>
  )
}
